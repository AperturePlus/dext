/** Coarse async mutex serializing Controller ticks, UI commands, and nav events.
 *  The critical section is sub-second; long waits (capture) happen outside the
 *  lock (spec §2.2). The returned release fn must be called exactly once;
 *  callers use try/finally. Not reentrant — the Controller never re-acquires
 *  under itself. */

export interface Mutex {
  acquire(): Promise<() => void>;
}

export function createMutex(): Mutex {
  let locked = false;
  const waiters: Array<() => void> = [];

  function release(): void {
    locked = false;
    if (waiters.length > 0) {
      const next = waiters.shift();
      if (next) {
        locked = true;
        next();
      }
    }
  }

  return {
    acquire() {
      return new Promise<() => void>((resolve) => {
        if (locked) {
          waiters.push(() => resolve(release));
        } else {
          locked = true;
          resolve(release);
        }
      });
    },
  };
}
