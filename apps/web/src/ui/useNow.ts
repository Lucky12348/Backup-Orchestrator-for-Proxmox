import { useEffect, useState } from "react";

/**
 * The current time, refreshed every minute: relative labels ("today 19:00",
 * "yesterday") are computed in the browser's time zone at render, so a page
 * left open overnight must re-render to stay right. `fixed` (tests) wins.
 */
export function useNow(fixed?: Date, intervalMs = 60_000): Date {
  const [now, setNow] = useState(() => fixed ?? new Date());
  useEffect(() => {
    if (fixed) return;
    const id = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(id);
  }, [fixed, intervalMs]);
  return fixed ?? now;
}
