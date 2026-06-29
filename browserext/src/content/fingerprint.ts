/** Form-action preparation fingerprint (amend §5.1). The fingerprint is SHA-256
 *  over a canonical JSON snapshot of the form's identity + submission attributes +
 *  the FetchAction input + the successful controls (virtual-applied, no DOM mutation).
 *  prepareFormAction and the CS perform step MUST use the SAME implementation, so a
 *  mismatch between prepare-time and perform-time proves the form changed (amend §5.3).
 *
 *  canonicalizeActionSnapshot is pure + synchronous + deterministic (sorted keys, DOM
 *  order for controls). sha256Hex is async (crypto.subtle) and injectable for tests. */

import type { FetchAction } from '../shared/types.js';

export interface ActionSnapshotControl {
  name: string;
  type: string;
  value: string;
}
export interface ActionSnapshotFileMeta {
  name: string;
  size: number;
  type: string;
  lastModified: number;
}
export interface ActionSnapshotInputElement {
  name: string;
  type: string;
  value: string;
  disabled?: boolean;
  checked?: boolean;
  // file inputs expose files (a list of {name,size,type,lastModified}); we record meta only
  files?: Array<{ name: string; size: number; type: string; lastModified: number }>;
  form?: { name: string } | null;
}
export interface ActionSnapshotForm {
  _index: number;          // index in document.forms
  name: string;
  id: string | null;
  action: string;
  method: string;
  enctype: string;
  target: string;
  elements: { length: number; [index: number]: ActionSnapshotInputElement | { value: string } };
}
export interface ActionSnapshotDoc {
  location: { href: string };
}

export interface PreparationSnapshot {
  form: { index: number; name: string; id: string | null };
  submission: { action: string; method: string; enctype: string; target: string };
  actionInput: { fields: Record<string, string>; submit: boolean };
  successfulControls: Array<[string, string, string]>;   // [name, type, value]
}

/** Build the canonical snapshot object (deterministic). */
function buildSnapshot(form: ActionSnapshotForm, action: FetchAction, doc: ActionSnapshotDoc): PreparationSnapshot {
  const baseUrl = doc.location.href;
  const resolvedAction = resolveUrl(form.action || baseUrl, baseUrl);
  const method = (form.method || 'get').toUpperCase();
  const enctype = form.enctype || '';
  const target = form.target || '';

  // action.fields sorted by key, serialized as an object so JSON.stringify yields
  // {"key":"value"} (insertion order = sorted). buildSnapshot inserts in sorted order.
  const fieldsEntries = Object.entries(action.fields ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const fields: Record<string, string> = {};
  for (const [k, v] of fieldsEntries) fields[k] = v;
  const fieldOverrides = new Map<string, string>(fieldsEntries);

  const successfulControls: Array<[string, string, string]> = [];
  for (let i = 0; i < form.elements.length; i++) {
    const el = form.elements[i] as ActionSnapshotInputElement | undefined;
    if (!el || !el.name) continue;
    if (el.disabled) continue;
    const type = (el.type || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio') {
      if (el.checked === false) continue;
    }
    const override = fieldOverrides.get(el.name);
    const value = override !== undefined ? override : el.value;
    successfulControls.push([el.name, type, value]);
  }

  return {
    form: { index: form._index, name: form.name, id: form.id },
    submission: { action: resolvedAction, method, enctype, target },
    actionInput: { fields, submit: action.submit !== false },
    successfulControls,
  };
}

function resolveUrl(ref: string, base: string): string {
  try {
    return new URL(ref, base).toString();
  } catch {
    return ref;
  }
}

/** Canonicalize to a stable UTF-8 JSON string (sorted top-level keys; fields sorted;
 *  controls in DOM order). The exact key ordering is part of the contract — prepare
 *  and perform must serialize identically. */
export function canonicalizeActionSnapshot(form: ActionSnapshotForm, action: FetchAction, doc: ActionSnapshotDoc): string {
  const snap = buildSnapshot(form, action, doc);
  return stableJson(snap);
}

/** Stable JSON: object keys emitted in insertion order EXCEPT actionInput.fields which
 *  is pre-sorted; arrays preserve order. No replacer reordering needed because
 *  buildSnapshot already sorted fields and kept DOM order for controls. */
function stableJson(value: unknown): string {
  return JSON.stringify(value);
}

/** SHA-256(text) → lowercase hex. Uses crypto.subtle by default (available in both the
 *  content-script DOM global and MV3 SW). Injectable for deterministic tests: the
 *  hashFn returns the fingerprint string directly (default path computes real hex
 *  from the ArrayBuffer digest). */
export async function sha256Hex(
  text: string,
  hashFn?: (data: Uint8Array) => Promise<string>,
): Promise<string> {
  const data = new TextEncoder().encode(text);
  if (hashFn) return hashFn(data);
  const digest = await crypto.subtle.digest('SHA-256', data);
  const bytes = new Uint8Array(digest);
  let hex = '';
  for (const b of bytes) hex += b.toString(16).padStart(2, '0');
  return hex;
}
