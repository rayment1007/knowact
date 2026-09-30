import { useCallback, useEffect, useRef, useState } from "react";
import { invalidateApiCache, matchesPaths, peekCache, subscribeCacheInvalidation } from "@/api/cache";

/** Keeps a mounted view current after relevant edits; a remount reuses session data. */
export function useCachedQuery<T>(path: string, read: () => Promise<T>) {
  const [state, setState] = useState<{ path: string; data?: T; error: boolean }>({ path, data: peekCache<T>(path), error: false });
  const reader = useRef(read);
  reader.current = read;
  useEffect(() => {
    let active = true;
    let generation = 0;
    const load = async () => {
      const request = ++generation;
      try {
        const data = await reader.current();
        if (active && request === generation) setState({ path, data, error: false });
      } catch (error) {
        if (active && request === generation && !(error instanceof DOMException && error.name === "AbortError")) {
          setState(previous => ({ path, data: previous.path === path ? previous.data : undefined, error: true }));
        }
      }
    };
    void load();
    const unsubscribe = subscribeCacheInvalidation(paths => { if (matchesPaths(path, paths)) void load(); });
    return () => { active = false; unsubscribe(); };
  }, [path]);
  const retry = useCallback(() => invalidateApiCache([path]), [path]);
  return { data: state.path === path ? state.data : peekCache<T>(path), error: state.path === path && state.error, retry };
}
