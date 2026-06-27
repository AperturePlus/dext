/** Per-job counter & verdict-sent persistence over an injectable chrome.storage.local-shaped area.
 * All state lives in storage (service workers are evicted; no in-memory state survives). */

export const REDIRECT_DEBOUNCE_MS = 15_000;

export interface StorageArea {
  get(keys: string | string[] | null): Promise<Record<string, unknown>>;
  set(obj: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

export interface Storage {
  bumpCount(jobId: string): Promise<number>;
  getCount(jobId: string): Promise<number>;
  markVerdictSent(jobId: string): Promise<void>;
  wasVerdictSent(jobId: string): Promise<boolean>;
  recordRedirect(jobId: string): Promise<void>;
  shouldRedirect(jobId: string, nowMs?: number): Promise<boolean>;
  clear(jobId: string): Promise<void>;
}

const COUNT_KEY = (id: string) => `count:${id}`;
const VERDICT_KEY = (id: string) => `verdict:${id}`;
const REDIRECT_KEY = (id: string) => `redirect:${id}`;

export function createChromeStorage(area: StorageArea): Storage {
  async function bumpCount(jobId: string): Promise<number> {
    const next = (await getCount(jobId)) + 1;
    await area.set({ [COUNT_KEY(jobId)]: next });
    return next;
  }
  async function getCount(jobId: string): Promise<number> {
    const obj = await area.get(COUNT_KEY(jobId));
    const v = obj[COUNT_KEY(jobId)];
    return typeof v === 'number' ? v : 0;
  }
  async function markVerdictSent(jobId: string): Promise<void> {
    await area.set({ [VERDICT_KEY(jobId)]: true });
  }
  async function wasVerdictSent(jobId: string): Promise<boolean> {
    const obj = await area.get(VERDICT_KEY(jobId));
    return obj[VERDICT_KEY(jobId)] === true;
  }
  async function recordRedirect(jobId: string): Promise<void> {
    await area.set({ [REDIRECT_KEY(jobId)]: Date.now() });
  }
  async function shouldRedirect(jobId: string, nowMs: number = Date.now()): Promise<boolean> {
    const obj = await area.get(REDIRECT_KEY(jobId));
    const last = obj[REDIRECT_KEY(jobId)];
    if (typeof last !== 'number') return true;
    return nowMs - last >= REDIRECT_DEBOUNCE_MS;
  }
  async function clear(jobId: string): Promise<void> {
    await area.remove([COUNT_KEY(jobId), VERDICT_KEY(jobId), REDIRECT_KEY(jobId)]);
  }
  return { bumpCount, getCount, markVerdictSent, wasVerdictSent, recordRedirect, shouldRedirect, clear };
}
