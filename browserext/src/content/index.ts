/** Content-script entry (spec §4, §5.2). Runs at document_start on <all_urls>.
 *  GATED: when EXCLUSIVE_CONTROL_ENABLED is false (official slice 1–5 builds),
 *  it does nothing — no marker, no RPC, no backend call. When true, it sets the
 *  data-dext-extension-controller marker on <html> FIRST (before any other
 *  action; spec §4.6), then early-returns on disallowed hosts (no panel/RPC).
 *  Slice 1 stops at the marker + host gate; PAGE_READY/panel/capture are
 *  slices 4–5. bootstrapContent is exported with an injectable opts object so
 *  tests don't need a real DOM. */

import { isAllowedFetchHost } from '../shared/hostPolicy.js';

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
}

export async function bootstrapContent(deps: ContentDeps): Promise<void> {
  if (!EXCLUSIVE_CONTROL_ENABLED) return;          // official build: pure no-op
  // 1. Marker FIRST, before any other action (spec §4.6).
  deps.documentElement.setAttribute(MARKER_ATTR, MARKER_VALUE);
  // 2. Early-return on disallowed host — no panel, no RPC, no backend (spec §5.2).
  if (!isAllowedFetchHost(deps.hostname)) return;
  // 3. Allowed host: slice 1 stops here. Slices 4–5 add the lifecycle.
  deps.onAllowedHost?.();
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
