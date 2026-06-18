const BLOCKED_HOSTS = new Set([
  'dx.scu.edu.cn',
  'mail.scu.edu.cn',
]);

const WECHAT_HOSTS = new Set(['mp.weixin.qq.com', 'weixin.qq.com']);

export function isAssistantBlockedHost(hostname: string = window.location.hostname): boolean {
  return BLOCKED_HOSTS.has((hostname || '').toLowerCase());
}

export function isWechatHost(hostname: string = window.location.hostname): boolean {
  return WECHAT_HOSTS.has((hostname || '').toLowerCase());
}

export function isWechatUrl(url: string): boolean {
  try {
    return isWechatHost(new URL(url).hostname);
  } catch {
    return false;
  }
}

const ALLOWED_FETCH_HOST_SUFFIXES = ['edu.cn', 'github.io'];

/**
 * Mirrors the backend ``dext.url_policy.is_allowed_fetch_host`` host check.
 * Deliberately excludes ``ac.cn`` and uses dot-boundary matching so that
 * ``notedu.cn`` / ``evilgithub.io`` are rejected. Takes a bare hostname
 * (not a full URL).
 */
export function isAllowedFetchHost(hostname: string = window.location.hostname): boolean {
  const host = (hostname || '').toLowerCase();
  if (!host) return false;
  return ALLOWED_FETCH_HOST_SUFFIXES.some(
    (suffix) => host === suffix || host.endsWith(`.${suffix}`),
  );
}

/**
 * A host we landed on after an offsite redirect from an allowed fetch host.
 * Wechat hosts and any non-allowlisted host count — on these the userscript
 * runs its lightweight "stray redirect" branch instead of full capture mode.
 */
export function isDisallowedRedirectHost(hostname: string = window.location.hostname): boolean {
  return isWechatHost(hostname) || !isAllowedFetchHost(hostname);
}
