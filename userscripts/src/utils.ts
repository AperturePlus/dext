export function urlMatches(a: string, b: string): boolean {
  try {
    const u1 = new URL(a);
    const u2 = new URL(b);
    // Ignore protocol (http vs https) — many university sites redirect.
    return (
      u1.hostname === u2.hostname &&
      u1.pathname.replace(/\/+$/, '') === u2.pathname.replace(/\/+$/, '') &&
      normalizedSearch(u1) === normalizedSearch(u2)
    );
  } catch {
    return false;
  }
}

function normalizedSearch(url: URL): string {
  if (!url.search) return '';
  const params = [...url.searchParams.entries()].sort(([aKey, aValue], [bKey, bValue]) => {
    const keyOrder = aKey.localeCompare(bKey);
    return keyOrder || aValue.localeCompare(bValue);
  });
  return params.map(([key, value]) => `${key}=${value}`).join('&');
}

/** Match hostname only (ignoring protocol and path). Handles www prefix and http/https redirects. */
export function sameHost(a: string, b: string): boolean {
  try {
    const h1 = new URL(a).hostname.replace(/^www\./, '');
    const h2 = new URL(b).hostname.replace(/^www\./, '');
    return h1 === h2;
  } catch {
    return false;
  }
}

/** Loose check: same site root (e.g. both *.pku.edu.cn). */
export function sameSite(a: string, b: string): boolean {
  try {
    return siteRoot(new URL(a).hostname) === siteRoot(new URL(b).hostname);
  } catch {
    return false;
  }
}

function siteRoot(host: string): string {
  const parts = host.split('.');
  // Handle .edu.cn / .ac.cn style TLDs
  if (parts.length >= 3 && parts.at(-1) === 'cn' && ['edu', 'ac', 'com'].includes(parts.at(-2)!)) {
    return parts.slice(-3).join('.');
  }
  return parts.slice(-2).join('.');
}

export function truncUrl(url: string, max = 40): string {
  try {
    return new URL(url).pathname.slice(0, max);
  } catch {
    return url.slice(0, max);
  }
}

const ERROR_PATTERNS = /502 bad gateway|503 service|504 gateway|500 internal|error occurred|server error|nginx/i;
const NOT_FOUND_PATTERNS = /\b404\b|not found|page not found|页面不存在|网页不存在|未找到页面|找不到页面|访问的页面不存在|您访问的页面不存在|信息不存在|该信息不存在|文章不存在/i;
const REMOVED_PATTERNS = /内容已撤销|内容被撤销|该内容已被删除|内容已被删除|文章已被删除|信息已被删除|该信息已删除|已下线|页面已下线|内容已失效/i;

export type TerminalUnavailableReason = 'not_found' | 'content_removed' | 'empty_page';

/** Detect if the current page is a server error page (502, 503, etc.). */
export function isErrorPage(): boolean {
  const title = document.title || '';
  const bodyText = document.body?.innerText || '';
  // Short page with error keywords = error page
  if (bodyText.length < 2000 && ERROR_PATTERNS.test(title + ' ' + bodyText)) return true;
  return false;
}

/** Detect terminal unavailable pages that should be skipped rather than retried. */
export function terminalUnavailableReason(): TerminalUnavailableReason | null {
  const title = document.title || '';
  const bodyText = document.body?.innerText || '';
  const haystack = `${title} ${bodyText}`;
  if (bodyText.length < 4000 && NOT_FOUND_PATTERNS.test(haystack)) return 'not_found';
  if (bodyText.length < 4000 && REMOVED_PATTERNS.test(haystack)) return 'content_removed';
  if (
    document.readyState === 'complete'
    && document.body !== null
    && bodyText.trim().length === 0
    && document.links.length === 0
  ) {
    return 'empty_page';
  }
  return null;
}
