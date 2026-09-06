/**
 * Client-side session resolution for the dashboard.
 *
 * The API authenticates every workspace route with an `X-Api-Key` header, so
 * the browser needs both a workspace id and a key before it can load
 * anything. Both resolve in the same order:
 *
 *   1. a `?api_key=` / `?workspace_id=` deep link, which is then persisted so
 *      a generated link keeps working after the query string is gone;
 *   2. a previously persisted value in localStorage;
 *   3. a build-time NEXT_PUBLIC_* default.
 *
 * Values are read through `useSyncExternalStore` rather than an effect that
 * calls setState. localStorage is external, browser-only state: the store
 * gives the server a defined snapshot (the build-time default), so there is
 * no hydration mismatch and no cascading render.
 */
"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

export const API_KEY_STORAGE_KEY = "sentinex_api_key";
export const WORKSPACE_STORAGE_KEY = "sentinex_workspace_id";

const BUILD_DEFAULTS: Record<string, string> = {
  [API_KEY_STORAGE_KEY]: process.env.NEXT_PUBLIC_SENTINEX_API_KEY || "",
  [WORKSPACE_STORAGE_KEY]: process.env.NEXT_PUBLIC_SENTINEX_WORKSPACE_ID || "",
};

// Subscribers are notified when this tab writes a value; the `storage` event
// covers writes made by other tabs.
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function readStorage(key: string): string {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    // Private mode or blocked site data — fall back to the build-time value.
    return "";
  }
}

export function writeSessionValue(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Persistence is a convenience; failing to store must not break the page.
  }
  emit();
}

/**
 * Resolve one session value, persisting a deep-link override when present.
 */
export function useSessionValue(
  storageKey: string,
  fromQuery: string | null
): string {
  // Persist the deep-link value before render reads the store. This is a
  // write, not a setState, so it does not cascade renders.
  useEffect(() => {
    if (fromQuery && readStorage(storageKey) !== fromQuery) {
      writeSessionValue(storageKey, fromQuery);
    }
  }, [storageKey, fromQuery]);

  const getSnapshot = useCallback(
    () => readStorage(storageKey) || BUILD_DEFAULTS[storageKey] || "",
    [storageKey]
  );
  const getServerSnapshot = useCallback(
    () => BUILD_DEFAULTS[storageKey] || "",
    [storageKey]
  );

  const stored = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  // Use the deep-link value immediately, before the effect above lands.
  return fromQuery || stored;
}

export function useApiKey(fromQuery: string | null = null): string {
  return useSessionValue(API_KEY_STORAGE_KEY, fromQuery);
}

export function useWorkspaceId(fromQuery: string | null = null): string {
  return useSessionValue(WORKSPACE_STORAGE_KEY, fromQuery);
}
