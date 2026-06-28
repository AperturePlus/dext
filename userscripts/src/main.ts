import { recoverState, skipStrayRedirectJob, startAutoWatcher, startPolling, stopAutoWatcher, stopPolling } from './actions';
import { readNavigationAttempt } from './actions';
import { isAssistantBlockedHost, isAllowedFetchHost, isWechatHost } from './hostPolicy';
import { startHeartbeat, stopHeartbeat } from './heartbeat';
import { startInstanceLock, stopInstanceLock } from './instanceLock';
import { cleanupLegacyStorage, hydratePrefs, setInstanceRole, state, subscribe } from './state';
import { mountPanel, renderPanel } from './ui/panel';
import { mountToast, showToast } from './ui/toast';

function isTopFrame(): boolean {
  try {
    return window.top === window.self;
  } catch {
    return false;
  }
}

async function waitForBody(): Promise<void> {
  if (document.body) return;
  await new Promise<void>((resolve) => {
    const onReady = () => resolve();
    document.addEventListener('DOMContentLoaded', onReady, { once: true });
  });
}

async function onRoleChange(role: 'owner' | 'standby'): Promise<void> {
  const previousRole = state.instanceRole;
  setInstanceRole(role);
  if (role === 'owner') {
    startHeartbeat();
    await recoverState();
    startPolling();
    startAutoWatcher();
    if (previousRole !== 'owner') {
      showToast('已接管为主实例');
    }
    return;
  }
  stopHeartbeat();
  stopPolling();
  stopAutoWatcher();
}

async function fullBootstrap(): Promise<void> {
  await cleanupLegacyStorage();
  await hydratePrefs();
  mountToast();
  mountPanel();
  subscribe(renderPanel);
  renderPanel();
  startInstanceLock((role) => {
    void onRoleChange(role);
  });
  window.addEventListener('beforeunload', () => {
    stopHeartbeat();
    stopInstanceLock();
  });
}

/**
 * Lightweight branch for disallowed redirect hosts (wechat / non-edu landing
 * pages reached after an offsite redirect from an allowed host). Does NOT mount
 * UI, claim the instance lock, or poll for new jobs — it only checks whether
 * this tab is a stray navigation for an in-flight job and skips that job.
 */
async function lightweightRedirectBootstrap(): Promise<void> {
  const attempt = readNavigationAttempt();
  if (!attempt) return;
  let targetHost = '';
  try {
    targetHost = new URL(attempt.targetUrl).hostname;
  } catch {
    return;
  }
  if (!isAllowedFetchHost(targetHost)) return;
  const reason = isWechatHost() ? 'wechat_redirect' : 'offsite_redirect';
  const ok = await skipStrayRedirectJob(reason);
  if (ok) {
    showToast(reason === 'wechat_redirect' ? '检测到微信公众号重定向，已跳过当前任务' : '检测到站外重定向，已跳过当前任务');
  }
}

async function bootstrap(): Promise<void> {
  // Phase-2 exclusive-control marker: if the dext extension owns this tab,
  // stand down immediately (spec §5.1). The marker is page-priority, not a
  // failover signal — its presence means the extension's content script is
  // already running here.
  if (
    document.documentElement?.getAttribute('data-dext-extension-controller') === 'v1'
  ) {
    return;
  }
  if (isAssistantBlockedHost() || !isTopFrame()) return;
  await waitForBody();
  if (isAllowedFetchHost()) {
    await fullBootstrap();
    return;
  }
  await lightweightRedirectBootstrap();
}

void bootstrap();
