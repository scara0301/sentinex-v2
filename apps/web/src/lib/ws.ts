/**
 * WebSocket hook for real-time scan event streaming.
 *
 * Auto-reconnects with exponential backoff and replays missed events via
 * `resume_from` using the highest seq seen so far.
 *
 * Reconnects are driven by an attempt counter rather than by the connect
 * function calling itself. That keeps the socket lifecycle inside a single
 * effect: the effect owns exactly one socket and tears it down in its own
 * cleanup, so a retry can never leave an earlier socket alive. The previous
 * version closed the old socket from a shared helper, which fired that
 * socket's `onclose` and scheduled a *second* reconnect on top of the one
 * already in flight — connections multiplied on every retry.
 */
"use client";

import { useEffect, useRef, useState } from "react";

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
  apiKey?: string;
  enabled?: boolean;
}

// Matches the API's WS auth subprotocol marker (scans_ws.py). Sending the key
// as a subprotocol keeps it out of the URL and server access logs.
const API_KEY_SUBPROTOCOL = "sentinex-api-key";

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

const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 16000;
const PING_INTERVAL = 25000;

export function useScanWS({
  workspaceId,
  scanId,
  onEvent,
  apiKey,
  enabled = true,
}: UseScanWSOptions) {
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    enabled ? "connecting" : "disconnected"
  );
  // Bumped by the retry timer; a change re-runs the effect and opens a new
  // socket after the previous one has been fully torn down.
  const [attempt, setAttempt] = useState(0);

  const lastSeqRef = useRef<number>(0);
  const reconnectDelayRef = useRef<number>(INITIAL_RECONNECT_DELAY);
  const onEventRef = useRef(onEvent);

  // Keep the callback fresh without making it a dependency of the socket
  // effect, which would reconnect on every parent render.
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    if (!enabled) return;

    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    // Guards every handler: after cleanup runs, this socket must not touch
    // React state or schedule work.
    let active = true;

    const url = `${WS_BASE}/workspace/${workspaceId}/scan/${scanId}/live`;
    const ws = apiKey
      ? new WebSocket(url, [API_KEY_SUBPROTOCOL, apiKey])
      : new WebSocket(url);

    ws.onopen = () => {
      if (!active) return;
      setConnectionState("connected");
      reconnectDelayRef.current = INITIAL_RECONNECT_DELAY;

      // Replay anything missed while disconnected, then continue live.
      if (lastSeqRef.current > 0) {
        ws.send(JSON.stringify({ resume_from: lastSeqRef.current }));
      }

      pingTimer = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, PING_INTERVAL);
    };

    ws.onmessage = (e) => {
      if (!active) return;
      try {
        const data: WSEvent = JSON.parse(e.data);

        if (typeof data.seq === "number" && data.seq > lastSeqRef.current) {
          lastSeqRef.current = data.seq;
        }

        // Drop control frames before forwarding to the consumer.
        if ("type" in data && data.type === "pong") return;
        if ("_replay_done" in data) return;

        onEventRef.current(data);
      } catch {
        // Ignore malformed messages rather than tearing down the stream.
      }
    };

    ws.onclose = () => {
      if (!active) return;
      setConnectionState("disconnected");
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }

      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY);
      reconnectTimer = setTimeout(() => {
        if (!active) return;
        setConnectionState("connecting");
        setAttempt((n) => n + 1);
      }, delay);
    };

    ws.onerror = () => {
      // Let onclose own the retry path; closing here funnels both cases
      // through the same backoff.
      ws.close();
    };

    return () => {
      active = false;
      if (pingTimer) clearInterval(pingTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      // Detach before closing so this socket's onclose cannot schedule a
      // reconnect that races the one this cleanup is making way for.
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.close();
    };
  }, [workspaceId, scanId, apiKey, enabled, attempt]);

  // A disabled hook reads as disconnected without needing an effect to
  // push that into state.
  return { connectionState: enabled ? connectionState : "disconnected" };
}
