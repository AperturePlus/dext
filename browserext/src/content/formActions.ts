/** Form-action logic — a byte-aligned port of userscripts/src/formPagination.ts
 *  (spec §4.1) PLUS the new side-effect-free prepare stage (amend §5.1). The
 *  byte-aligned collectFormPaginationStates / performFetchAction / buildSyntheticUrl
 *  read the same fields the userscript reads off `document`, but from an injected
 *  `doc` so they are unit-testable. The synthetic_url construction MUST byte-match
 *  the userscript (the duplicate-graph-node bug trap).
 *
 *  prepareFormAction (amend §5.1) is NEW: it finds the form, parses action/method,
 *  computes targetUrl (GET merges successful controls + FetchAction.fields into a
 *  query URL so §2.1 requestId URL-match holds; POST uses the parsed absolute
 *  form.action), and computes preparationFingerprint = sha256Hex(canonical snapshot).
 *  It mutates NOTHING. targetUrl must pass allowed-host + same-crawl-site gate or
 *  the action is NOT performed (offsite skip). */

import type { FetchAction, PaginationState } from '../shared/types.js';
import { isAllowedFetchHost } from '../shared/hostPolicy.js';
import { sameCrawlSite } from '../shared/urlGate.js';
import { canonicalizeActionSnapshot } from './fingerprint.js';
import { sha256Hex } from './fingerprint.js';

const PAGE_ASSIGN_RE =
  /document\.forms\[['"]([^'"]+)['"]\]\.([A-Za-z0-9_]+)\.value\s*=\s*['"]?(\d+)['"]?/i;
const GOTO_FIELD_RE = /\b([A-Za-z0-9_]*?)GOPAGE\b/i;

export interface FormElement {
  name: string;
  type: string;
  value: string;
  form?: { name: string } | null;
  disabled?: boolean;
  checked?: boolean;
  // for multiple-select/checkbox we read `.value` like the userscript does
}
export interface FormEl {
  // The fields here are a superset of what the byte-aligned ports read (name,
  // action, method, elements, submit) PLUS the fields ActionSnapshotForm requires
  // for prepareFormAction's canonicalizeActionSnapshot call (_index, id, enctype,
  // target). Structurally compatible with fingerprint.ActionSnapshotForm.
  _index: number;
  name: string;
  id: string | null;
  action: string;
  method: string;
  enctype: string;
  target: string;
  elements: { namedItem(name: string): FormElement | FormElementCollection | null; length: number; [index: number]: FormElement | FormElementCollection };
  submit(): void;
}
export interface FormElementCollection {
  value: string;
  // RadioNodeList-like; the userscript reads `.value`
}
export interface FormDoc {
  forms: { namedItem(name: string): FormEl | null; length: number; [index: number]: FormEl };
  querySelectorAll(selector: string): Array<{ getAttribute?(n: string): string | null; name?: string; form?: { name: string } | null; textContent?: string }>;
  querySelector(selector: string): { textContent: string } | null;
  links: { length: number };
  location: { href: string };
}

export function collectFormPaginationStates(currentUrl: string, doc: FormDoc): PaginationState[] {
  if (hasExplicitPort(currentUrl)) return [];
  const anchors = doc.querySelectorAll('a[href^="javascript:"]');
  const byFormField = new Map<string, { formName: string; fieldName: string; pages: Set<number> }>();

  for (const anchor of anchors) {
    const parsed = parsePageAssignment(anchor.getAttribute?.('href') ?? '');
    if (!parsed) continue;
    const key = `${parsed.formName} ${parsed.fieldName}`;
    const existing = byFormField.get(key) ?? { formName: parsed.formName, fieldName: parsed.fieldName, pages: new Set<number>() };
    existing.pages.add(parsed.pageIndex);
    byFormField.set(key, existing);
  }

  const currentPage = detectCurrentPage(currentUrl, doc);
  const states: PaginationState[] = [];
  const seen = new Set<string>();
  for (const item of byFormField.values()) {
    const pageIndexes = expandPageIndexes(item.formName, item.fieldName, item.pages, doc);
    const totalPages = Math.max(...pageIndexes, ...item.pages);
    for (const pageIndex of pageIndexes) {
      if (pageIndex <= 1 || pageIndex === currentPage) continue;
      const syntheticUrl = buildSyntheticUrl(currentUrl, item.formName, item.fieldName, pageIndex);
      if (seen.has(syntheticUrl)) continue;
      seen.add(syntheticUrl);
      states.push({
        kind: 'form_submit',
        state_id: `form:${item.formName}:${item.fieldName}:${pageIndex}`,
        label: `${item.formName} 第 ${pageIndex} 页`,
        page_index: pageIndex,
        total_pages: totalPages,
        form_name: item.formName,
        fields: { [item.fieldName]: String(pageIndex) },
        submit: true,
        synthetic_url: syntheticUrl,
        url: currentUrl,
      });
    }
  }

  return states.sort((a, b) => a.page_index - b.page_index || a.synthetic_url.localeCompare(b.synthetic_url));
}

function hasExplicitPort(url: string): boolean {
  const raw = url.trim();
  const scheme = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.exec(raw);
  let rest = '';
  if (scheme) {
    rest = raw.slice(scheme[0].length);
  } else if (raw.startsWith('//')) {
    rest = raw.slice(2);
  } else {
    return false;
  }
  const authority = rest.split(/[/?#]/, 1)[0] ?? '';
  const hostport = authority.split('@').at(-1) ?? '';
  if (hostport.startsWith('[')) {
    const closing = hostport.indexOf(']');
    return closing !== -1 && hostport.slice(closing + 1).startsWith(':');
  }
  return hostport.includes(':');
}

export function buildSyntheticUrl(url: string, formName: string, fieldName: string, pageIndex: number): string {
  const parsed = new URL(url);
  for (const key of [...parsed.searchParams.keys()]) {
    if (key.startsWith('__ycl_')) parsed.searchParams.delete(key);
  }
  parsed.searchParams.set('__ycl_kind', 'form');
  parsed.searchParams.set('__ycl_form', formName);
  parsed.searchParams.set('__ycl_field', fieldName);
  parsed.searchParams.set('__ycl_page', String(pageIndex));
  parsed.hash = '';
  return parsed.toString();
}

export function performFetchAction(action: FetchAction | null | undefined, doc: FormDoc): boolean {
  if (!action || action.kind !== 'form_submit') return false;
  const formName = action.form_name || '';
  const form = doc.forms.namedItem(formName);
  if (!form) return false;
  for (const [name, value] of Object.entries(action.fields ?? {})) {
    const control = form.elements.namedItem(name);
    if (!control) continue;
    setControlValue(control, value);
  }
  if (action.submit !== false) {
    form.submit();
  }
  return true;
}

function setControlValue(control: FormElement | FormElementCollection, value: string): void {
  if ('value' in control) {
    (control as FormElement).value = value;
  }
}

function parsePageAssignment(href: string): { formName: string; fieldName: string; pageIndex: number } | null {
  const match = PAGE_ASSIGN_RE.exec(href);
  if (!match) return null;
  const pageIndex = Number(match[3]);
  if (!Number.isFinite(pageIndex) || pageIndex <= 0) return null;
  return { formName: match[1] ?? '', fieldName: match[2] ?? '', pageIndex };
}

function expandPageIndexes(formName: string, fieldName: string, pages: Set<number>, doc: FormDoc): number[] {
  const inputs = doc.querySelectorAll('input[name]');
  const hasGoto = inputs.some((input) => {
    const name = input.name || '';
    const match = GOTO_FIELD_RE.exec(name);
    if (!match) return false;
    const prefix = match[1] || '';
    const form = input.form;
    return (!form || form.name === formName) && (!prefix || fieldName.toLowerCase().startsWith(prefix.toLowerCase()));
  });
  if (!hasGoto) return [...pages].sort((a, b) => a - b);
  const maxPage = Math.max(...pages);
  return Array.from({ length: maxPage }, (_unused, index) => index + 1);
}

function detectCurrentPage(currentUrl: string, doc: FormDoc): number {
  const current = Number(doc.querySelector('.this-page')?.textContent?.trim() || '0');
  if (Number.isFinite(current) && current > 0) return current;
  try {
    const url = new URL(currentUrl);
    for (const key of ['PAGENUM', 'page', 'p', 'pn', 'fromWenNOWPAGE']) {
      const value = Number(url.searchParams.get(key) || '0');
      if (Number.isFinite(value) && value > 0) return value;
    }
  } catch {
    // ignore
  }
  return 1;
}

// ---- NEW: side-effect-free prepare stage (amend §5.1) ----

export interface PrepareResult {
  ok: boolean;
  targetUrl?: string;
  method?: string;
  expectedEffect?: 'new_document' | 'same_document' | 'unknown';
  preparationFingerprint?: string;
  error?: string;
}

/** Resolve a form action's absolute target URL from the parsed form.action + the
 *  document's base URL (mirrors how the browser resolves a relative form action). */
function resolveActionUrl(formAction: string, baseUrl: string): string {
  try {
    return new URL(formAction, baseUrl).toString();
  } catch {
    return formAction;
  }
}

/** Compute the query URL a GET form would actually request after merging
 *  FetchAction.fields into the successful controls (no DOM mutation). */
function buildGetTargetUrl(baseUrl: string, form: FormEl, action: FetchAction): string {
  const target = resolveActionUrl(form.action || baseUrl, baseUrl);
  const u = new URL(target);
  // start from the form's existing successful controls (virtual apply)
  for (const [name, value] of virtualSuccessfulControls(form, action)) {
    u.searchParams.set(name, value);
  }
  return u.toString();
}

/** Virtual successful-controls snapshot WITHOUT mutating the DOM (amend §5.1):
 *  applies FetchAction.fields on top, ignores disabled/unnamed/unchecked, keeps
 *  duplicate names and multiple-select values. Returns [name, value] tuples in DOM order. */
function virtualSuccessfulControls(form: FormEl, action: FetchAction): Array<[string, string]> {
  const fieldOverrides = new Map<string, string>();
  for (const [k, v] of Object.entries(action.fields ?? {})) fieldOverrides.set(k, v);
  const out: Array<[string, string]> = [];
  for (let i = 0; i < form.elements.length; i++) {
    const el = form.elements[i] as FormElement | undefined;
    if (!el || !el.name) continue;
    if (el.disabled) continue;
    const type = (el.type || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio') {
      if (el.checked === false) continue;
    }
    const override = fieldOverrides.get(el.name);
    const value = override !== undefined ? override : el.value;
    out.push([el.name, value]);
  }
  return out;
}

/** Side-effect-free prepare (amend §5.1). Returns the parsed action + fingerprint.
 *  Mutates NOTHING. ok===false when the form is missing, the target fails the
 *  host/same-site gate (offsite_redirect — do NOT perform), or the form cannot be
 *  parsed. expectedEffect: 'new_document' for POST / non-trivial method, 'same_document'
 *  heuristic otherwise (the CS may refine via navigationExpected at perform time). */
export async function prepareFormAction(action: FetchAction, doc: FormDoc): Promise<PrepareResult> {
  if (!action || action.kind !== 'form_submit') return { ok: false, error: 'not_form_submit' };
  const formName = action.form_name || '';
  const form = doc.forms.namedItem(formName);
  if (!form) return { ok: false, error: 'form_not_found' };
  const baseUrl = doc.location.href;
  const method = (form.method || 'get').toUpperCase();
  let targetUrl: string;
  if (method === 'GET') {
    targetUrl = buildGetTargetUrl(baseUrl, form, action);
  } else {
    targetUrl = resolveActionUrl(form.action || baseUrl, baseUrl);
  }
  // allowed-host + same-crawl-site gate (amend §5.1): offsite target → do not perform.
  try {
    const host = new URL(targetUrl).hostname.toLowerCase();
    if (!isAllowedFetchHost(host)) return { ok: false, error: 'offsite_redirect' };
    if (!sameCrawlSite(targetUrl, baseUrl)) return { ok: false, error: 'offsite_redirect' };
  } catch {
    return { ok: false, error: 'invalid_target_url' };
  }
  const snapshot = canonicalizeActionSnapshot(form, action, doc);
  const preparationFingerprint = await sha256Hex(snapshot);
  const expectedEffect: 'new_document' | 'same_document' | 'unknown' =
    method === 'GET' ? 'same_document' : 'new_document';
  return { ok: true, targetUrl, method, expectedEffect, preparationFingerprint };
}
