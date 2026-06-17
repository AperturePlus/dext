import { failCurrent, openCurrent, overrideUrl, skipCurrent, submitCurrent, switchPendingDecisionToHuman } from '../actions';
import { setAutoMode, setMinimized, state, togglePaused } from '../state';
import { urlMatches } from '../utils';
import {
  renderActions,
  renderDecision,
  renderEmpty,
  renderHeader,
  renderJobDetail,
  renderMatchBanner,
  renderStandbyBanner,
  renderToggles,
} from './panelSections';
import { getShadowRoot, mountShadowHost } from './shadowHost';

let panelEl: HTMLDivElement | null = null;

export function mountPanel(): void {
  const shadow = mountShadowHost();
  panelEl = shadow.querySelector('#ycl-panel') as HTMLDivElement | null;
  panelEl?.addEventListener('click', (event) => {
    if (state.minimized && event.target === event.currentTarget) {
      setMinimized(false);
    }
  });
}

export function renderPanel(): void {
  if (!panelEl) return;

  if (state.minimized) {
    panelEl.className = 'ycl-minimized';
    panelEl.innerHTML = '';
    return;
  }
  panelEl.className = '';

  const isStandby = state.instanceRole !== 'owner';
  const job = state.currentJob;
  const matched = job != null && urlMatches(window.location.href, job.url);

  panelEl.innerHTML = [
    renderHeader(),
    isStandby ? renderStandbyBanner() : '',
    renderDecision(isStandby),
    matched ? renderMatchBanner() : '',
    job ? renderJobDetail(job) : renderEmpty(isStandby),
    job ? renderActions(isStandby) : '',
    renderToggles(isStandby),
  ].join('');

  bindEvents();
}

function bindEvents(): void {
  const shadow = getShadowRoot();
  const bind = (id: string, event: string, fn: () => void) => {
    shadow?.getElementById(id)?.addEventListener(event, fn);
  };
  const job = state.currentJob;

  shadow?.getElementById('ycl-min')?.addEventListener('click', (event) => {
    event.stopPropagation();
    setMinimized(true);
  });

  bind('ycl-copy', 'click', () => {
    if (job) void navigator.clipboard.writeText(job.url);
  });
  bind('ycl-open', 'click', () => {
    openCurrent();
  });
  bind('ycl-submit', 'click', submitCurrent);
  bind('ycl-skip', 'click', skipCurrent);
  bind('ycl-fail', 'click', () => failCurrent());
  bind('ycl-override', 'click', overrideUrl);
  bind('ycl-decision-switch', 'click', () => {
    void switchPendingDecisionToHuman();
  });
  bind('ycl-auto', 'change', () => {
    setAutoMode(!state.autoMode);
  });
  bind('ycl-pause', 'change', () => {
    togglePaused();
  });
}
