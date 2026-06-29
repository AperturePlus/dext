/** Content-script work-RPC router (amend §4.1, §4.3, §5.1, §5.3, §2.3). Receives
 *  PREPARE_ACTION / PERFORM_ACTION / CAPTURE from the SW, drives the ported form/capture/
 *  pageDetect modules + the rpcId ledger, and emits ACTION_PREPARED / ACTION_RESULT /
 *  CAPTURE_RESULT (with capture-before-second-detection). It returns a transport receipt
 *  { received: true } synchronously-ish (amend §1.1) — the operation RESULT is a separate
 *  later message. Dependency-injected so node tests drive it with a fake document + runtime.
 *
 *  The router does NOT call the backend, does NOT navigate (form.submit() runs only inside
 *  performFetchAction, dispatched by the SW's PERFORM_ACTION which the SW persisted-before),
 *  and holds NO authoritative job state. */

import { prepareFormAction, performFetchAction, collectFormPaginationStates } from './formActions.js';
import type { FormDoc } from './formActions.js';
import { detectPage } from './pageDetect.js';
import { serializeWithoutOverlay } from './capture.js';
import { createRpcLedger } from '../shared/rpcLedger.js';
import type { RpcLedger } from '../shared/rpcLedger.js';
import type { SwToCs, CsToSw, MessageReceipt, ActionPrepared, ActionResult, CaptureResult } from '../shared/rpc.js';

export interface RpcRouterDoc extends FormDoc {
  title: string;
  bodyText: string;
  readyState: 'loading' | 'interactive' | 'complete';
  linksLength: number;
  bodyNull: boolean;
  documentElement: {
    querySelectorAll(selector: string): Array<{ tagName?: string; getAttribute(n: string): string | null; remove(): void; querySelectorAll(selector: string): Array<unknown> }>;
    cloneNode(deep: boolean): {
      outerHTML: string;
      querySelectorAll(selector: string): Array<{ tagName?: string; getAttribute(n: string): string | null; remove(): void; querySelectorAll(selector: string): Array<unknown> }>;
    };
  };
}
export interface RpcRouterRuntime {
  sendMessage(message: CsToSw): Promise<unknown>;
  onMessage(cb: (message: SwToCs, sender: { tab?: { id: number }; frameId?: number; documentId?: string }) => void | Promise<MessageReceipt>): void;
}
export interface RpcRouterDeps {
  document: RpcRouterDoc;
  runtime: RpcRouterRuntime;
  now?: () => number;
}

export interface RpcRouter {
  handle(message: SwToCs, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<MessageReceipt>;
}

export function createRpcRouter(deps: RpcRouterDeps): RpcRouter {
  const doc = deps.document;
  const runtime = deps.runtime;
  const ledger: RpcLedger = createRpcLedger();

  async function emit(message: CsToSw): Promise<void> {
    await runtime.sendMessage(message);
  }

  async function handlePrepare(msg: Extract<SwToCs, { op: 'PREPARE_ACTION' }>): Promise<MessageReceipt> {
    const prepared = await prepareFormAction(msg.action, doc);
    const out: ActionPrepared = {
      op: 'ACTION_PREPARED',
      rpcId: msg.rpcId,
      jobId: msg.jobId,
      ok: prepared.ok,
      targetUrl: prepared.targetUrl,
      method: prepared.method,
      expectedEffect: prepared.expectedEffect,
      preparationFingerprint: prepared.preparationFingerprint,
      error: prepared.error,
    };
    await emit(out);
    return { received: true };
  }

  async function handlePerform(msg: Extract<SwToCs, { op: 'PERFORM_ACTION' }>): Promise<MessageReceipt> {
    // amend §4.3: repeats never re-execute.
    const existing = ledger.get(msg.rpcId);
    if (existing?.stage === 'done') {
      await emit(existing.result);
      return { received: true };
    }
    if (existing?.stage === 'received') {
      // already executing or receipted; just confirm receipt (result will come)
      return { received: true };
    }
    ledger.markReceived(msg.rpcId);
    // amend §5.3: re-validate fingerprint by recomputing prepare.
    const recomputed = await prepareFormAction(msg.action, doc);
    if (!recomputed.ok || recomputed.preparationFingerprint !== msg.preparationFingerprint) {
      const mismatch: ActionResult = {
        op: 'ACTION_RESULT', rpcId: msg.rpcId, jobId: msg.jobId,
        ok: false, invoked: false, navigationExpected: false, effectApplied: false,
        error: 'form_action_prepare_changed',
      };
      ledger.markDone(msg.rpcId, mismatch);
      await emit(mismatch);
      return { received: true };
    }
    const invoked = performFetchAction(msg.action, doc);
    const navigationExpected = recomputed.expectedEffect !== 'same_document';
    const effectApplied = recomputed.expectedEffect === 'same_document';
    const result: ActionResult = {
      op: 'ACTION_RESULT', rpcId: msg.rpcId, jobId: msg.jobId,
      ok: invoked, invoked, navigationExpected, effectApplied,
      error: invoked ? undefined : 'form_action_invoke_failed',
    };
    ledger.markDone(msg.rpcId, result);
    await emit(result);
    return { received: true };
  }

  async function handleCapture(msg: Extract<SwToCs, { op: 'CAPTURE' }>): Promise<MessageReceipt> {
    // amend §2.3: second detection before returning HTML.
    const detection = detectPage({
      title: doc.title,
      bodyText: doc.bodyText,
      readyState: doc.readyState,
      linksLength: doc.linksLength,
      bodyNull: doc.bodyNull,
    });
    const failed = detection.errorPage || detection.terminalReason !== null;
    if (failed) {
      const result: CaptureResult = {
        op: 'CAPTURE_RESULT', rpcId: msg.rpcId, jobId: msg.jobId, ok: false,
        url: doc.location.href, detection,
        error: detection.terminalReason ?? 'error_page',
      };
      await emit(result);
      return { received: true };
    }
    const html = serializeWithoutOverlay(doc.documentElement, { stripHeader: false });
    const paginationStates = collectFormPaginationStates(doc.location.href, doc);
    const result: CaptureResult = {
      op: 'CAPTURE_RESULT', rpcId: msg.rpcId, jobId: msg.jobId, ok: true,
      url: doc.location.href, html, title: doc.title, paginationStates, detection,
    };
    await emit(result);
    return { received: true };
  }

  return {
    async handle(message, _sender) {
      switch (message.op) {
        case 'PREPARE_ACTION': return handlePrepare(message);
        case 'PERFORM_ACTION': return handlePerform(message);
        case 'CAPTURE': return handleCapture(message);
        default: return { received: true };
      }
    },
  };
}
