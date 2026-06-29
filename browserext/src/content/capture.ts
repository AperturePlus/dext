/** HTML capture cleanup + serialization — a byte-aligned port of
 *  userscripts/src/htmlCleanup.ts (spec §4.1). Pure: takes an injected root
 *  (a ParentNode-shaped object) instead of `document`/`documentElement`, so it is
 *  unit-testable. The structural-noise stripping (footer/header by name/role/class)
 *  is byte-aligned with the userscript, including the "only the FIRST class segment
 *  identifies a structural role" guard (the NEU `wrapper header` regression) and the
 *  "header that swallows the page body is left intact" guard (the LZU regression).
 *
 *  The stability-wait logic (waitForCaptureReady) is split into a pure
 *  isCaptureStable decider + a DOM-driving wrapper; only the decider is unit-tested. */

export interface CaptureCleanupOptions {
  stripHeader?: boolean;
}

type StructuralNoiseKind = 'footer' | 'header';

export interface ElementDescriptor {
  tagName?: string | null;
  role?: string | null;
  id?: string | null;
  className?: string | null;
}

const FOOTER_NAMES = new Set(['footer', 'foot', 'copyright', 'copy-right', 'site-footer', 'page-footer']);
const FOOTER_BOUNDARY_NAMES = new Set(['footer', 'foot', 'copyright', 'copy-right']);
const HEADER_NAMES = new Set(['header', 'head', 'site-header', 'page-header', 'topbar', 'top-bar', 'nav-header']);
const HEADER_BOUNDARY_NAMES = new Set(['header', 'topbar', 'top-bar']);

const FOOTER_ROLES = new Set(['contentinfo']);
const HEADER_ROLES = new Set(['banner']);

const HEADER_SWALLOW_RATIO = 0.5;

/** Minimal injected-root shape — only what stripCaptureNoise touches. */
interface CaptureRoot {
  querySelectorAll(selector: string): Array<{
    tagName?: string;
    getAttribute(n: string): string | null;
    remove(): void;
    querySelectorAll(selector: string): Array<unknown>;
  }>;
}

export function stripCaptureNoise(root: CaptureRoot, options: CaptureCleanupOptions = {}): void {
  root.querySelectorAll('#ycl-panel,#ycl-toast,[data-yanclaw-overlay]').forEach((node) => node.remove());
  root.querySelectorAll('svg,style,canvas').forEach((node) => node.remove());
  stripStructuralNoise(root, 'footer');
  if (options.stripHeader) {
    stripStructuralNoise(root, 'header');
  }
}

export function shouldStripElementDescriptor(kind: StructuralNoiseKind, descriptor: ElementDescriptor): boolean {
  const tagName = (descriptor.tagName ?? '').toLowerCase();
  const role = (descriptor.role ?? '').toLowerCase();
  if (kind === 'footer') {
    return (
      tagName === 'footer'
      || FOOTER_ROLES.has(role)
      || attributeHasStructuralName(descriptor.id, FOOTER_NAMES, FOOTER_BOUNDARY_NAMES)
      || attributeHasStructuralName(descriptor.className, FOOTER_NAMES, FOOTER_BOUNDARY_NAMES)
    );
  }
  return (
    tagName === 'header'
    || HEADER_ROLES.has(role)
    || attributeHasStructuralName(descriptor.id, HEADER_NAMES, HEADER_BOUNDARY_NAMES)
    || attributeHasStructuralName(descriptor.className, HEADER_NAMES, HEADER_BOUNDARY_NAMES)
  );
}

function stripStructuralNoise(root: CaptureRoot, kind: StructuralNoiseKind): void {
  const rootDescendantCount = root.querySelectorAll('*').length;
  root.querySelectorAll('*').forEach((node) => {
    if (shouldStripElementNode(kind, node)) {
      if (kind === 'header' && wouldSwallowPageBody(node, rootDescendantCount)) {
        return;
      }
      node.remove();
    }
  });
}

function wouldSwallowPageBody(header: { querySelectorAll(sel: string): Array<unknown> }, rootDescendantCount: number): boolean {
  if (rootDescendantCount === 0) return false;
  const headerDescendantCount = header.querySelectorAll('*').length;
  return headerDescendantCount / rootDescendantCount >= HEADER_SWALLOW_RATIO;
}

function shouldStripElementNode(kind: StructuralNoiseKind, element: Element | { tagName?: string; getAttribute(n: string): string | null }): boolean {
  return shouldStripElementDescriptor(kind, {
    tagName: typeof (element as Element).tagName === 'string' ? (element as Element).tagName : undefined,
    role: element.getAttribute('role'),
    id: element.getAttribute('id'),
    className: element.getAttribute('class'),
  });
}

function attributeHasStructuralName(
  value: string | null | undefined,
  names: Set<string>,
  boundaryNames: Set<string>,
): boolean {
  if (!value) return false;
  const segments = value.trim().split(/\s+/);
  if (segments.length === 0) return false;
  return segmentMatchesName(segments[0] ?? '', names, boundaryNames);
}

function segmentMatchesName(segment: string, names: Set<string>, boundaryNames: Set<string>): boolean {
  const canonical = canonicalizeIdentifier(segment);
  if (!canonical) return false;
  if (matchesName(canonical, names)) return true;
  return canonical.split('-').some((token) => matchesName(token, boundaryNames));
}

function matchesName(value: string, names: Set<string>): boolean {
  return names.has(value) || names.has(value.replaceAll('-', ''));
}

function canonicalizeIdentifier(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Serialize the (cloned) root with overlay/noise stripped — byte-aligned with the
 *  userscript serializePageWithoutOverlay. The caller passes the real
 *  document.documentElement (or a fake root in tests). */
export function serializeWithoutOverlay(
  root: CaptureRoot & { cloneNode(deep: boolean): CaptureRoot & { outerHTML: string } },
  options: { stripHeader: boolean },
): string {
  const clone = root.cloneNode(true);
  stripCaptureNoise(clone, options);
  return clone.outerHTML;
}

/** Pure decider for the capture-stability loop (waitForCaptureReady). The DOM-driving
 *  wrapper computes a signature (textLength:nodeCount:imageCount) each round and calls
 *  this; stable when the signature is unchanged AND stableRounds >= threshold. */
export function isCaptureStable(prev: string, curr: string, stableRounds: number, threshold: number): boolean {
  return prev === curr && stableRounds >= threshold;
}
