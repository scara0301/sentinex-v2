/**
 * EventStream — real-time scrolling event list with type-colored badges.
 *
 * Renders WebSocket events in a virtualized-style scrollable panel.
 * New events slide in from the bottom with animation.
 */
"use client";

import { useEffect, useRef } from "react";
import type { WSEvent } from "@/lib/ws";
import styles from "./EventStream.module.css";

interface EventStreamProps {
  events: WSEvent[];
  maxVisible?: number;
}

const TYPE_LABELS: Record<string, string> = {
  tool_call: "TOOL CALL",
  tool_result: "TOOL RESULT",
  llm_message: "LLM",
  finding: "FINDING",
  state: "STATE",
  risk_update: "RISK",
  scenario_step: "SCENARIO",
  breakpoint: "BREAK",
  log: "LOG",
};

function formatTime(ts: string | null): string {
  if (!ts) return "--:--:--";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "--:--:--";
  }
}

function summarizePayload(type: string, payload: Record<string, unknown>): string {
  switch (type) {
    case "tool_call":
      return `${payload.tool ?? "unknown"} → ${JSON.stringify(payload.args ?? {}).slice(0, 80)}`;
    case "tool_result":
      return `${payload.ok ? "✓" : "✗"} ${payload.duration_ms ?? 0}ms${payload.injected ? " [INJECTED]" : ""}`;
    case "llm_message":
      return `[${payload.role ?? "?"}] ${String(payload.content ?? "").slice(0, 100)}`;
    case "finding":
      return `${payload.severity?.toString().toUpperCase()} — ${payload.title ?? payload.rule_id}`;
    case "state": {
      const from = (payload as Record<string, string>).from_ ?? (payload as Record<string, string>).from ?? "?";
      return `${from} → ${payload.to}`;
    }
    case "risk_update":
      return `Score: ${Number(payload.score ?? 0).toFixed(1)} (${Number(payload.delta ?? 0) >= 0 ? "+" : ""}${Number(payload.delta ?? 0).toFixed(1)})`;
    case "scenario_step":
      return `${payload.scenario} step ${payload.step}: ${payload.name} [${payload.result}]`;
    case "log":
      return `[${payload.level}] ${payload.message}`;
    default:
      return JSON.stringify(payload).slice(0, 100);
  }
}

export default function EventStream({
  events,
  maxVisible = 200,
}: EventStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  // Auto-scroll when new events arrive (if user is near bottom)
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events.length]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distFromBottom < 60;
  };

  const visible = events.slice(-maxVisible);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h3>Event Stream</h3>
        <span className={styles.counter}>{events.length} events</span>
      </div>
      <div
        className={styles.scrollArea}
        ref={scrollRef}
        onScroll={handleScroll}
      >
        {visible.length === 0 ? (
          <div className="empty-state">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
              <polyline points="13 2 13 9 20 9" />
            </svg>
            <p>Waiting for events…</p>
          </div>
        ) : (
          visible.map((ev, i) => (
            <div
              key={`${ev.seq}-${i}`}
              className={`${styles.event} animate-slide-in`}
              style={{ animationDelay: `${Math.min(i * 20, 200)}ms` }}
            >
              <span className={styles.time}>{formatTime(ev.ts)}</span>
              <span className={`badge badge-event badge-${ev.type}`}>
                {TYPE_LABELS[ev.type] ?? ev.type.toUpperCase()}
              </span>
              <span className={styles.summary}>
                {summarizePayload(ev.type, ev.payload)}
              </span>
              {ev._replay && (
                <span className={styles.replayTag}>replay</span>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
