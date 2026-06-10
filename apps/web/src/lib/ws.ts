/**
 * WebSocket hook for real-time scan event streaming.
 *
 * Auto-reconnects with exponential backoff and replays missed events
 * via `resume_from` using the highest seq seen so far.
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ConnectionState = "connecting" | "connected" | "disconnected";

export interface WSEvent {
  scan_id: string;
  seq: number;
  ts: string | null;
  type: string;
  payload: Record<string, unknown>;
  _replay?: boolean;
  _replay_done?: boolean;
  last_seq?: number;
}

interface UseScanWSOptions {
  workspaceId: string;
  scanId: string;
  onEvent: (event: WSEvent) => void;
  enabled?: boolean;
}

// Resolution order: explicit WS URL -> derived from the API URL
// (https -> wss) -> dev fallback. `||` (not ??) so an empty inlined
// env var falls through.
const WS_BASE =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_WS_URL ||
      (process.env.NEXT_PUBLIC_API_URL
        ? process.env.NEXT_PUBLIC_API_URL.replace(/^http/, "ws")
        : `ws://${window.location.hostname}:8000`)
    : "ws://localhost:8000";

const MAX_RECONNECT_DELAY = 16000;
const PING_INTERVAL = 25000;

export function useScanWS({
  workspaceId,
  scanId,
  onEvent,
  enabled = true,
}: UseScanWSOptions) {
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const lastSeqRef = useRef<number>(0);
  const reconnectDelayRef = useRef<number>(1000);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onEventRef = useRef(onEvent);

  // Keep callback ref fresh without triggering reconnects.
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const cleanup = useCallback(() => {
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const enabledRef = useRef(enabled);
  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  const connect = useCallback(() => {
    if (!enabled) return;
    cleanup();
    setConnectionState("connecting");

    const url = `${WS_BASE}/workspace/${workspaceId}/scan/${scanId}/live`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnectionState("connected");
      reconnectDelayRef.current = 1000;

      if (lastSeqRef.current > 0) {
        ws.send(JSON.stringify({ resume_from: lastSeqRef.current }));
      }

      pingTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, PING_INTERVAL);
    };

    ws.onmessage = (e) => {
      try {
        const data: WSEvent = JSON.parse(e.data);

        if (typeof data.seq === "number" && data.seq > lastSeqRef.current) {
          lastSeqRef.current = data.seq;
        }

        // Drop control frames before forwarding to consumer.
        if ("type" in data && data.type === "pong") return;
        if ("_replay_done" in data) return;

        onEventRef.current(data);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnectionState("disconnected");
      if (pingTimerRef.current) {
        clearInterval(pingTimerRef.current);
      }

      if (enabledRef.current) {
        const delay = reconnectDelayRef.current;
        reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY);
        reconnectTimerRef.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [workspaceId, scanId, enabled, cleanup]);

  useEffect(() => {
    if (enabled) {
      connect();
    }
    return cleanup;
  }, [connect, cleanup, enabled]);

  return { connectionState };
}
