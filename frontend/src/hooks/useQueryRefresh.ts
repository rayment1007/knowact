import { useEffect, useRef } from "react";
import { matchesPaths, subscribeCacheInvalidation } from "@/api/cache";

/** Bridge for existing editable module screens; preserves forms while reads update. */
export function useQueryRefresh(path: string, refresh: () => unknown) {
  const callback = useRef(refresh);
  callback.current = refresh;
  useEffect(() => subscribeCacheInvalidation(paths => {
    if (matchesPaths(path, paths)) void callback.current();
  }), [path]);
}
