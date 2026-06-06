import { useEffect, useRef } from "react";

export function usePolling(fn: () => void, intervalMs: number) {
  // eslint-disable-next-line react-hooks/refs
  const ref = useRef(fn);
  // eslint-disable-next-line react-hooks/refs
  ref.current = fn;
  useEffect(() => {
    fn();
    const id = setInterval(() => ref.current(), intervalMs);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs]);
}
