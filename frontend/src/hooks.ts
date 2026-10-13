// Data hooks.  One poll shape and one one-shot shape; both report loading,
// error and freshness so every surface can render all four lifecycle states.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, UnauthorizedError, describeError } from './api';

export interface Query<T> {
  data: T | null;
  error: { title: string; detail: string; term?: string } | null;
  loading: boolean;
  updatedAt: number | null;
  reload: () => Promise<void>;
}

// A 401 from any hook (in any page) must raise the token banner.  The App
// registers the one handler; hooks notify it.
type UnauthorizedListener = () => void;
let unauthorizedListener: UnauthorizedListener | null = null;

export function registerUnauthorizedListener(listener: UnauthorizedListener | null) {
  unauthorizedListener = listener;
}

export function notifyUnauthorized() {
  unauthorizedListener?.();
}

function useQuery<T>(path: string | null, intervalMs: number | null): Query<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Query<T>['error']>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const generation = useRef(0);

  const load = useCallback(async () => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    const mine = ++generation.current;
    try {
      const payload = await api<T>(path);
      if (generation.current !== mine) return;
      setData(payload);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (err) {
      if (generation.current !== mine) return;
      if (err instanceof UnauthorizedError) notifyUnauthorized();
      else setError(describeError(err));
    } finally {
      if (generation.current === mine) setLoading(false);
    }
  }, [path]);

  // A path change invalidates the previous query's payload immediately:
  // rendering candidate A's evidence under candidate B's selection is worse
  // than a skeleton. Declared before the load effect so it runs first.
  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(Boolean(path));
  }, [path]);

  useEffect(() => {
    void load();
    if (!intervalMs || !path) return undefined;
    const timer = window.setInterval(() => void load(), intervalMs);
    return () => window.clearInterval(timer);
  }, [load, intervalMs, path]);

  return { data, error, loading, updatedAt, reload: load };
}

/** Poll a path.  Pass `null` to suspend the query. */
export function usePoll<T>(path: string | null, intervalMs = 2000): Query<T> {
  return useQuery<T>(path, intervalMs);
}

/** Fetch a path once per path change. */
export function useOnce<T>(path: string | null): Query<T> {
  return useQuery<T>(path, null);
}
