/** Content-script entry (spec §4, §5.2). Runs at document_start on <all_urls>.
 *  GATED: when EXCLUSIVE_CONTROL_ENABLED is false (official slice 1–5 builds),
 *  it does nothing — no marker, no RPC, no backend call. When true, it sets the
 *  data-dext-extension-controller marker on <html> FIRST (before any other
 *  action; spec §4.6), then early-returns on disallowed hosts (no panel/RPC).
 *  Slice 4 registers the work-RPC router (prepare/perform/capture) on allowed
 *  hosts and emits PAGE_READY (first detection). Slice 5 will add the panel.
 *  bootstrapContent is exported with an injectable opts object so tests don't
 *  need a real DOM. */

import { isAllowedFetchHost } from '../shared/hostPolicy.js';
import { detectPage } from './pageDetect.js';
import { createRpcRouter } from './rpcRouter.js';
import type { RpcRouterDoc } from './rpcRouter.js';
import type { CsToSw, SwToCs, PanelState } from '../shared/rpc.js';
import { mountPanel } from './panel.js';
import { panelStateEqual } from './panelState.js';

declare const EXCLUSIVE_CONTROL_ENABLED: boolean;

const MARKER_ATTR = 'data-dext-extension-controller';
const MARKER_VALUE = 'v1';

export interface ContentDeps {
  hostname: string;
  documentElement: {
    setAttribute(name: string, value: string): void;
    getAttribute(name: string): string | null;
    removeAttribute(name: string): void;
  };
  now?: number;
  /** Called only on allowed fetch hosts, after the marker is set. Slice 1
   *  does nothing here; slices 4–5 hang PAGE_READY/panel/capture off it. */
  onAllowedHost?: () => void;
  /** Injectable for tests (node tests pass a fake). When omitted, the panel
   *  block falls back to the global `document`. Slice 5: panel mount + REGISTER
   *  + TICK + STATE_CHANGED render. */
  document?: Document;
  /** Injectable for tests. When omitted, falls back to the global `chrome`. */
  chrome?: typeof chrome;
}

/** Build a getter-backed snapshot of the live document so the router reads
 *  fresh values at handle (CAPTURE/PERFORM) time, not at construction time.
 *  Returns the RpcRouterDoc shape (plain string fields + the form/capture
 *  surfaces the ported modules read). */
function liveDocShim(): RpcRouterDoc {
  const d = document;
  return {
    get title() { return d.title || ''; },
    get bodyText() { return d.body?.innerText ?? ''; },
    get readyState() { return d.readyState as 'loading' | 'interactive' | 'complete'; },
    get linksLength() { return d.links?.length ?? 0; },
    get bodyNull() { return d.body === null; },
    forms: d.forms as unknown as RpcRouterDoc['forms'],
    querySelectorAll: (sel: string) => Array.from(d.querySelectorAll(sel)) as unknown as ReturnType<RpcRouterDoc['querySelectorAll']>,
    querySelector: (sel: string) => d.querySelector(sel) as unknown as ReturnType<RpcRouterDoc['querySelector']>,
    links: d.links as unknown as RpcRouterDoc['links'],
    location: d.location as unknown as RpcRouterDoc['location'],
    documentElement: d.documentElement as unknown as RpcRouterDoc['documentElement'],
  };
}

export async function bootstrapContent(deps: ContentDeps): Promise<void> {
  // GATED: official slice 1–5 builds define EXCLUSIVE_CONTROL_ENABLED=false, so this
  // whole block is dead — no marker, no RPC, no backend. Wrap the body in the
  // positive form `if (GATE) {...}` (NOT `if (!GATE) return`) so esbuild emits
  // `if (false) {...}` and the body is skipped at runtime even un-minified.
  if (EXCLUSIVE_CONTROL_ENABLED) {
    // 1. Marker FIRST, before any other action (spec §4.6).
    deps.documentElement.setAttribute(MARKER_ATTR, MARKER_VALUE);
    // 2. Early-return on disallowed host — no panel, no RPC, no backend (spec §5.2).
    if (!isAllowedFetchHost(deps.hostname)) return;
    // 3. Allowed host. Slice 4: register the work-RPC router + emit PAGE_READY.
    deps.onAllowedHost?.();
    if (typeof chrome !== 'undefined' && chrome.runtime) {
      const router = createRpcRouter({
        document: liveDocShim(),
        runtime: {
          sendMessage: (m) => chrome.runtime.sendMessage(m),
          onMessage: (cb) => chrome.runtime.onMessage.addListener(cb as never),
        },
      });
      chrome.runtime.onMessage.addListener((msg: SwToCs, sender) => {
        if (msg && (msg.op === 'PREPARE_ACTION' || msg.op === 'PERFORM_ACTION' || msg.op === 'CAPTURE')) {
          void router.handle(msg, sender as { tab?: { id: number }; frameId?: number; documentId?: string });
          return true;   // async response (transport receipt comes via a later CAPTURE_RESULT/ACTION_* message)
        }
        return false;
      });
      // PAGE_READY: wait for body + DOMContentLoaded, then run the first detection + emit (slice 4).
      const sendReady = () => {
        const detection = detectPage({
          title: document.title || '',
          bodyText: document.body?.innerText ?? '',
          readyState: document.readyState as 'loading' | 'interactive' | 'complete',
          linksLength: document.links?.length ?? 0,
          bodyNull: document.body === null,
        });
        void chrome.runtime.sendMessage({
          op: 'PAGE_READY', url: location.href, title: document.title, detection,
        } as CsToSw);
      };
      if (document.readyState === 'loading' || !document.body) {
        document.addEventListener('DOMContentLoaded', sendReady, { once: true });
      } else {
        sendReady();
      }
    }
    // Slice 5: panel + REGISTER + 2s TICK + STATE_CHANGED render. Runs alongside
    // the slice-4 work-RPC listener + PAGE_READY emitter above (both coexist).
    // Resolved against deps first (node tests inject fakes), then the globals;
    // GUARDED so slice-1/4 node tests that pass NO document/chrome skip this
    // block without crashing — `doc?.body && chr?.runtime?.sendMessage` is the
    // gate. The panel reads the live override-URL input via a formData closure
    // over the shadow root (Task-3 follow-up: override was dead from real
    // clicks because mountPanel hard-coded `() => null`).
    const doc = deps.document ?? (typeof document !== 'undefined' ? document : undefined);
    const chr = deps.chrome ?? (typeof chrome !== 'undefined' ? chrome : undefined);
    if (doc?.body && chr?.runtime?.sendMessage) {
      let panel: ReturnType<typeof mountPanel> | null = null;
      try {
        const host = doc.createElement('div') as HTMLDivElement & { attachShadow: (i: { mode: 'open' }) => unknown };
        host.id = 'dext-panel-host';
        doc.body.appendChild(host);
        panel = mountPanel({ host: host as never }, (cmd) => {
          try {
            void chr.runtime.sendMessage({ op: 'COMMAND', command: cmd } as CsToSw);
          } catch { /* SW asleep */ }
        }, (id) => {
          const sh = (host as unknown as { __dextShadow?: { getElementById?(id: string): { value?: string } | null } }).__dextShadow;
          const el = sh?.getElementById?.(id);
          return el?.value ?? null;
        });
      } catch { panel = null; }
      if (panel) {
        // REGISTER once — the receipt carries the initial PanelState (render it).
        const url = (typeof location !== 'undefined' ? location.href : '');
        void chr.runtime.sendMessage({ op: 'REGISTER', url } as CsToSw)
          .then((receipt: unknown) => {
            const r = receipt as { state?: PanelState };
            if (r?.state) {
              try { panel!.render(r.state); } catch { /* panel unmounted */ }
            }
          })
          .catch(() => {});
        // 2s TICK — wake/reconcile the SW (MV3 doesn't guarantee the SW stays
        // alive; the TICK re-establishes intent + lets the SW reconcile state).
        // `.unref?.()` is a no-op in the browser (where setInterval returns a
        // number); in Node tests it drops the handle so the test process can exit
        // instead of hanging on a 2s interval that never fires during the test.
        const tick = setInterval(() => {
          try { void chr.runtime.sendMessage({ op: 'TICK' } as CsToSw); }
          catch { /* SW asleep — non-fatal */ }
        }, 2000);
        (tick as unknown as { unref?: () => void }).unref?.();
        // STATE_CHANGED → re-render (skip no-op re-renders via panelStateEqual).
        let last: PanelState | undefined;
        chr.runtime.onMessage.addListener((msg: SwToCs) => {
          if (msg && msg.op === 'STATE_CHANGED') {
            if (panelStateEqual(last, msg.state)) return false;
            last = msg.state;
            try { panel!.render(msg.state); } catch { /* panel unmounted */ }
          }
          return false;
        });
      }
    }
  }
}

// Browser entry: wire to the real document. Guarded so the module is importable
// in node tests (where document is undefined).
if (typeof document !== 'undefined' && document.documentElement) {
  void bootstrapContent({
    hostname: typeof location !== 'undefined' ? location.hostname : '',
    documentElement: document.documentElement,
    now: Date.now(),
  });
}
