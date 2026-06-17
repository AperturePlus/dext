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

export function stripCaptureNoise(root: ParentNode, options: CaptureCleanupOptions = {}): void {
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

function stripStructuralNoise(root: ParentNode, kind: StructuralNoiseKind): void {
  root.querySelectorAll('*').forEach((node) => {
    if (shouldStripElement(kind, node)) {
      node.remove();
    }
  });
}

function shouldStripElement(kind: StructuralNoiseKind, element: Element): boolean {
  return shouldStripElementDescriptor(kind, {
    tagName: element.tagName,
    role: element.getAttribute('role'),
    id: element.getAttribute('id'),
    className: element.getAttribute('class'),
  });
}

/**
 * Only the FIRST (primary) whitespace-separated segment identifies an element's
 * structural role. A layout wrapper such as `class="wrapper header"` must NOT be
 * stripped just because a later class segment names a header/footer — doing so
 * nukes the entire page body (the NEU CSE `wrapper header` regression, where
 * detail pages were captured as empty and permanently skipped).
 */
function attributeHasStructuralName(
  value: string | null | undefined,
  names: Set<string>,
  boundaryNames: Set<string>,
): boolean {
  if (!value) return false;
  const segments = value.trim().split(/\s+/);
  if (segments.length === 0) return false;
  return segmentMatchesName(segments[0], names, boundaryNames);
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
