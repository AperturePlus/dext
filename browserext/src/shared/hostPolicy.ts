/** Mirrors backend dext.url_policy.is_allowed_fetch_host and
 * userscripts/src/hostPolicy.ts. Deliberately excludes ac.cn and uses
 * dot-boundary matching so notedu.cn / evilgithub.io are rejected.
 * Pure — no DOM, no chrome. Lives in src/shared so both tsconfigs see it. */

const ALLOWED_FETCH_HOST_SUFFIXES = ['edu.cn', 'github.io'];

export function isAllowedFetchHost(hostname: string): boolean {
  const host = (hostname || '').toLowerCase();
  if (!host) return false;
  return ALLOWED_FETCH_HOST_SUFFIXES.some(
    (suffix) => host === suffix || host.endsWith(`.${suffix}`),
  );
}
