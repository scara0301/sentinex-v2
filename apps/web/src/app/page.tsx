/**
 * Dashboard home — recent scans with status and risk scores.
 *
 * This is a client component on purpose. Every workspace route on the API
 * requires an `X-Api-Key` header, and the key lives in the browser (deep
 * link or localStorage), so the request cannot be made during server
 * rendering. The previous server-side version fetched without the header,
 * got 422 every time, and swallowed the error — the list was always empty.
 */
"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

import { listScans, type ScanSummary } from "@/lib/api";
import { useApiKey, useWorkspaceId } from "@/lib/session";
import styles from "./page.module.css";

const REFRESH_MS = 10000;

function riskColor(score: number | null): string {
  if (score === null) return "var(--text-muted)";
  if (score >= 75) return "var(--severity-critical)";
  if (score >= 50) return "var(--severity-high)";
  if (score >= 25) return "var(--severity-medium)";
  if (score > 0) return "var(--severity-low)";
  return "var(--state-done)";
}

function statusClass(status: string): string {
  const map: Record<string, string> = {
    PENDING: "badge-pending",
    PROVISIONING: "badge-running",
    SEEDING: "badge-running",
    RUNNING: "badge-running",
    DRAINING: "badge-scoring",
    SCORING: "badge-scoring",
    REPORTING: "badge-scoring",
    DONE: "badge-done",
    FAILED: "badge-failed",
    PAUSED: "badge-pending",
  };
  return map[status] ?? "badge-pending";
}

function DashboardView() {
  const searchParams = useSearchParams();
  const workspaceId = useWorkspaceId(searchParams.get("workspace_id"));
  const apiKey = useApiKey(searchParams.get("api_key"));
  const [scans, setScans] = useState<ScanSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId || !apiKey) return;
    // `cancelled` keeps a response that arrives after unmount (or after the
    // workspace/key changed) from writing into stale state.
    let cancelled = false;

    const load = async () => {
      try {
        const rows = await listScans(workspaceId, apiKey);
        if (cancelled) return;
        setScans(rows);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        // Surface the failure rather than rendering a misleading empty list.
        setError(e instanceof Error ? e.message : String(e));
      }
    };

    const timer = setInterval(load, REFRESH_MS);
    void load();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [workspaceId, apiKey]);

  const configured = Boolean(workspaceId && apiKey);

  return (
    <main className="page-container">
      <section className={styles.hero}>
        <div className={styles.heroContent}>
          <h1>
            <span className={styles.gradient}>SENTINEX</span> Dashboard
          </h1>
          <p className={styles.heroSub}>
            AI Agent Runtime Security Platform — monitor, analyze, and secure
            your agents.
          </p>
        </div>
      </section>

      {!configured && (
        <div
          className="glass-card"
          style={{ padding: "1rem", marginBottom: "1rem" }}
        >
          <p>
            This dashboard needs a workspace and an API key. Append{" "}
            <code>?workspace_id=&lt;uuid&gt;&amp;api_key=&lt;key&gt;</code> to
            this URL and both will be remembered on this device, or set{" "}
            <code>NEXT_PUBLIC_SENTINEX_WORKSPACE_ID</code> and{" "}
            <code>NEXT_PUBLIC_SENTINEX_API_KEY</code> at build time.
          </p>
          <p
            style={{
              fontSize: "0.8125rem",
              color: "var(--text-muted)",
              marginTop: 8,
            }}
          >
            Create a workspace with <code>POST /workspace</code> — the response
            returns the id and key once.
          </p>
        </div>
      )}

      {error && (
        <div
          className="glass-card"
          style={{ padding: "1rem", marginBottom: "1rem" }}
        >
          <p style={{ color: "var(--severity-high)" }}>
            Could not load scans: {error}
          </p>
        </div>
      )}

      <section className={styles.statsGrid}>
        <div className={`glass-card ${styles.statCard}`}>
          <span className={styles.statValue}>{scans.length}</span>
          <span className={styles.statLabel}>Total Scans</span>
        </div>
        <div className={`glass-card ${styles.statCard}`}>
          <span className={styles.statValue}>
            {scans.filter((s) => s.status === "RUNNING").length}
          </span>
          <span className={styles.statLabel}>Active</span>
        </div>
        <div className={`glass-card ${styles.statCard}`}>
          <span className={styles.statValue}>
            {
              scans.filter((s) => s.risk_score !== null && s.risk_score >= 50)
                .length
            }
          </span>
          <span className={styles.statLabel}>High Risk</span>
        </div>
        <div className={`glass-card ${styles.statCard}`}>
          <span className={styles.statValue}>
            {scans.filter((s) => s.status === "DONE").length}
          </span>
          <span className={styles.statLabel}>Completed</span>
        </div>
      </section>

      <section>
        <h2 className={styles.sectionTitle}>Recent Scans</h2>
        {scans.length === 0 ? (
          <div className={`glass-card ${styles.emptyScans}`}>
            <div className="empty-state">
              <svg
                width="48"
                height="48"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
              <h3>{configured ? "No scans yet" : "Not connected"}</h3>
              <p style={{ fontSize: "0.8125rem", marginTop: 8 }}>
                {configured
                  ? "Upload an agent and start a scan to see results here."
                  : "Provide a workspace id and API key to load scans."}
              </p>
            </div>
          </div>
        ) : (
          <div className={styles.scanList}>
            {scans.map((scan) => (
              <Link
                key={scan.id}
                href={`/workspace/${workspaceId}/scan/${scan.id}/live`}
                className={`glass-card ${styles.scanCard}`}
              >
                <div className={styles.scanCardLeft}>
                  <code className={styles.scanId}>{scan.id.slice(0, 8)}</code>
                  <span className={`badge ${statusClass(scan.status)}`}>
                    {scan.status}
                  </span>
                </div>
                <div className={styles.scanCardRight}>
                  <span
                    className={styles.scanScore}
                    style={{ color: riskColor(scan.risk_score) }}
                  >
                    {scan.risk_score !== null
                      ? Number(scan.risk_score).toFixed(0)
                      : "—"}
                  </span>
                  <span className={styles.scanDate}>
                    {new Date(scan.created_at).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <main className="page-container">
          <p>Loading dashboard…</p>
        </main>
      }
    >
      <DashboardView />
    </Suspense>
  );
}
