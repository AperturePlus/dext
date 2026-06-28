/** Persists ControllerState to a single chrome.storage.local key so a SW can
 *  rehydrate across restarts. Change-gated writes (saveIfChanged) prevent the
 *  2s TICK from blindly writing storage every tick (spec §2.2 write-frequency
 *  invariant). Injectable area for tests. */

import { initialControllerState } from '../shared/state.js';
import type { ControllerState } from '../shared/state.js';

const STORAGE_KEY = 'dext_controller_state_v1';

export interface StorageArea {
  get(keys: string | string[] | null): Promise<Record<string, unknown>>;
  set(obj: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

export interface ControllerStorage {
  load(): Promise<ControllerState>;
  save(state: ControllerState): Promise<void>;
  saveIfChanged(prev: ControllerState, next: ControllerState): Promise<boolean>;
  clear(): Promise<void>;
}

export function createControllerStorage(area: StorageArea): ControllerStorage {
  async function load(): Promise<ControllerState> {
    const obj = await area.get(STORAGE_KEY);
    const v = obj[STORAGE_KEY];
    if (v && typeof v === 'object') {
      // shallow-validate the persisted shape; deeper validation is the Controller's job
      return structuredClone(v) as ControllerState;
    }
    return initialControllerState(Date.now());
  }

  async function save(state: ControllerState): Promise<void> {
    await area.set({ [STORAGE_KEY]: state });
  }

  async function saveIfChanged(prev: ControllerState, next: ControllerState): Promise<boolean> {
    if (JSON.stringify(prev) === JSON.stringify(next)) return false;
    await area.set({ [STORAGE_KEY]: next });
    return true;
  }

  async function clear(): Promise<void> {
    await area.remove(STORAGE_KEY);
  }

  return { load, save, saveIfChanged, clear };
}
