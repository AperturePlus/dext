/** Per-document in-memory rpcId ledger (amend §4.3). The content script keeps one
 *  per document lifecycle. For PERFORM_ACTION: insert {stage:'received'} before
 *  returning the transport receipt; the first message for an rpcId executes the action
 *  and caches a small ActionResult as {stage:'done'}; repeats for that rpcId NEVER
 *  re-execute the form — they re-emit the cached result (or just confirm receipt if
 *  no result yet). PREPARE_ACTION is side-effect-free and re-runnable (the router may
 *  cache or recompute). CAPTURE is side-effect-free; large HTML is NOT cached here.
 *  Pure — no chrome, no DOM, no persistence. */

import type { ActionResult } from './rpc.js';

export type RpcLedgerEntry =
  | { stage: 'received' }
  | { stage: 'done'; result: ActionResult };

export interface RpcLedger {
  has(rpcId: string): boolean;
  get(rpcId: string): RpcLedgerEntry | undefined;
  markReceived(rpcId: string): void;
  markDone(rpcId: string, result: ActionResult): void;
  clear(rpcId: string): void;
}

export function createRpcLedger(): RpcLedger {
  const entries = new Map<string, RpcLedgerEntry>();
  return {
    has(rpcId) { return entries.has(rpcId); },
    get(rpcId) { return entries.get(rpcId); },
    markReceived(rpcId) {
      if (!entries.has(rpcId)) entries.set(rpcId, { stage: 'received' });
    },
    markDone(rpcId, result) { entries.set(rpcId, { stage: 'done', result }); },
    clear(rpcId) { entries.delete(rpcId); },
  };
}
