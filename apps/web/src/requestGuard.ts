// Anti double click (B25): one request per key at a time, checked
// synchronously, before React re-renders the disabled button. Pure (node:test).

export interface RequestGuard {
  /** Runs `task` unless a request with the same key is in flight (then null). */
  run<T>(key: string, task: () => Promise<T>): Promise<T | null>;
  busy(key: string): boolean;
}

export function createRequestGuard(): RequestGuard {
  const inFlight = new Set<string>();
  return {
    async run(key, task) {
      if (inFlight.has(key)) return null;
      inFlight.add(key);
      try {
        return await task();
      } finally {
        inFlight.delete(key);
      }
    },
    busy: (key) => inFlight.has(key),
  };
}
