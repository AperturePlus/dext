/** Same-crawl-site URL gate + wechat/offsite redirect detection (spec §3.2 cond 3,
 *  amend §3.1, §3.5). Verbatim port of userscripts/src/utils.ts urlMatches /
 *  hasExplicitPort / siteRoot, plus the sameCrawlSite rule (github.io hosts must
 *  match EXACTLY — each *.github.io is a different user site; edu.cn shares the
 *  site root) and the wechat/offsite redirect classification from
 *  userscripts/src/main.ts. Pure — no DOM, no chrome. Slice-4 content reuses
 *  urlMatches for the form-action targetUrl match (amend §5.1). */

declare global {
  // WebWorker typings omit URLSearchParams.entries(); patch only that method so the
  // verbatim brief implementation below typechecks in the background project.
  interface URLSearchParams {
    entries(): IterableIterator<[string, string]>;
  }
}

const WECHAT_HOSTS = new Set(['mp.weixin.qq.com', 'weixin.qq.com']);

export function hasExplicitPort(url: string | URL): boolean {
  if (url instanceof URL) return url.port !== '';
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

function normalizedSearch(url: URL): string {
  if (!url.search) return '';
  const params = [...url.searchParams.entries()].sort(([aKey, aValue], [bKey, bValue]) => {
    const keyOrder = aKey.localeCompare(bKey);
    return keyOrder || aValue.localeCompare(bValue);
  });
  return params.map(([key, value]) => `${key}=${value}`).join('&');
}

/** Byte-aligned with userscripts/src/utils.ts urlMatches. */
export function urlMatches(a: string, b: string): boolean {
  try {
    if (hasExplicitPort(a) || hasExplicitPort(b)) return false;
    const u1 = new URL(a);
    const u2 = new URL(b);
    return (
      u1.hostname === u2.hostname &&
      u1.pathname.replace(/\/+$/, '') === u2.pathname.replace(/\/+$/, '') &&
      normalizedSearch(u1) === normalizedSearch(u2)
    );
  } catch {
    return false;
  }
}

export function siteRoot(host: string): string {
  const parts = host.split('.');
  // Handle .edu.cn / .ac.cn / .com.cn style TLDs (mirrors userscript utils.siteRoot)
  if (parts.length >= 3 && parts.at(-1) === 'cn' && ['edu', 'ac', 'com'].includes(parts.at(-2)!)) {
    return parts.slice(-3).join('.');
  }
  return parts.slice(-2).join('.');
}

/** github.io hosts must match EXACTLY (each *.github.io is a different user site);
 *  edu.cn hosts share the site root. Mirrors userscript sameSite + spec §3.2 cond 3. */
export function sameCrawlSite(a: string, b: string): boolean {
  try {
    if (hasExplicitPort(a) || hasExplicitPort(b)) return false;
    const u1 = new URL(a);
    const u2 = new URL(b);
    const h1 = u1.hostname.toLowerCase();
    const h2 = u2.hostname.toLowerCase();
    if (h1.endsWith('.github.io') || h1 === 'github.io' || h2.endsWith('.github.io') || h2 === 'github.io') {
      return h1 === h2;
    }
    return siteRoot(h1) === siteRoot(h2);
  } catch {
    return false;
  }
}

export function isWechatHost(hostname: string): boolean {
  return WECHAT_HOSTS.has(hostname.toLowerCase());
}

/** Classify a redirect target against the requested URL (amend §3.1, §3.5).
 *  wechat wins outright (terminal skip wechat_redirect, no budget); a redirect
 *  to a different crawl-site root is offsite (terminal skip offsite_redirect, no
 *  budget); otherwise onsite (keep waiting for the final onCompleted). */
export function redirectKind(redirectUrl: string, requestedUrl: string): 'onsite' | 'wechat' | 'offsite' {
  try {
    const host = new URL(redirectUrl).hostname.toLowerCase();
    if (isWechatHost(host)) return 'wechat';
  } catch {
    return 'offsite';   // unparseable redirect target → treat as offsite (safe skip)
  }
  return sameCrawlSite(redirectUrl, requestedUrl) ? 'onsite' : 'offsite';
}
