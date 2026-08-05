import { useEffect, useRef, useState } from "react";

interface PollState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Polls `fetcher` every `intervalMs`, ignoring results from requests that
 * were superseded by a newer one (e.g. a slow response arriving after a
 * faster later poll) so the UI never flickers backwards to stale data.
 *
 * Also returns `refetch()` to trigger an immediate poll on demand (e.g.
 * right after a mutation) without waiting for the next interval tick.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs: number): PollState<T> & { refetch: () => void } {
  const [state, setState] = useState<PollState<T>>({ data: null, error: null, loading: true });
  const [reloadToken, setReloadToken] = useState(0);
  const generation = useRef(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const myGeneration = ++generation.current;
      try {
        const data = await fetcherRef.current();
        if (!cancelled && myGeneration === generation.current) {
          setState({ data, error: null, loading: false });
        }
      } catch (err) {
        if (!cancelled && myGeneration === generation.current) {
          setState((prev) => ({ ...prev, error: err as Error, loading: false }));
        }
      }
    }

    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, reloadToken]);

  return { ...state, refetch: () => setReloadToken((t) => t + 1) };
}
