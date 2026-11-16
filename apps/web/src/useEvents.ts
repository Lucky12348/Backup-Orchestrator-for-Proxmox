import { useEffect, useRef, useState } from "react";

import { isConnected, onStatusChange, retainEvents, subscribe, type AppEvent, type AppEventType } from "./events";

/**
 * Subscribe to real-time events (SSE, shared connection).
 *
 *   const live = useEvents(["disk.added", "disk.removed"], (event) => …);
 *
 * The handler may change between renders (latest one is used). Returns
 * whether the stream is connected: callers keep their polling as a fallback
 * only while it is not.
 */
export function useEvents(types: AppEventType[] | "*", handler?: (event: AppEvent) => void): boolean {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  const [connected, setConnected] = useState(isConnected);
  const key = types === "*" ? "*" : [...types].sort().join(",");

  useEffect(() => {
    const release = retainEvents();
    const stopStatus = onStatusChange(setConnected);
    setConnected(isConnected());
    const list: (AppEventType | "*")[] = key === "*" ? ["*"] : (key.split(",") as AppEventType[]);
    const unsubscribers = list.map((type) => subscribe(type, (event) => handlerRef.current?.(event)));
    return () => {
      unsubscribers.forEach((unsubscribe) => unsubscribe());
      stopStatus();
      release();
    };
  }, [key]);

  return connected;
}
