/**
 * Dashboard Home — shows recent scans with status + risk scores.
 *
 * In production this would load workspace data from the API.
 * For now it renders a demo workspace with a scan list that
 * refreshes on an interval.
 */
import Link from "next/link";
import styles from "./page.module.css";

// Demo workspace ID — in production this comes from auth/session
const DEMO_WORKSPACE = "00000000-0000-0000-0000-000000000001";

interface ScanRow {
  id: string;
  status: string;
  risk_score: number | null;
  created_at: string;
}

async function fetchScans(): Promise<ScanRow[]> {
  try {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    const res = await fetch(
      `${apiUrl}/workspace/${DEMO_WORKSPACE}/scan`,
      { cache: "no-store" }
    );
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

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
  };
  return map[status] ?? "badge-pending";
}

export default async function DashboardPage() {
  const scans = await fetchScans();

  return (
    <main className="page-container">
      {/* Hero */}
      <section className={styles.hero}>
        <div className={styles.heroContent}>
          <h1>
            <span className={styles.gradient}>SENTINEX</span> Dashboard
          </h1>
          <p className={styles.heroSub}>
            AI Agent Runtime Security Platform — monitor, analyze, and secure your agents.
          </p>
        </div>
        <div className={styles.heroActions}>
          <Link
            href={`/workspace/${DEMO_WORKSPACE}/scan/demo/live`}
            className="btn btn-primary"
          >
            ▶ Live Demo
          </Link>
        </div>
      </section>

      {/* Stats */}
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
            {scans.filter((s) => s.risk_score !== null && s.risk_score >= 50).length}
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

      {/* Scan List */}
      <section>
        <h2 className={styles.sectionTitle}>Recent Scans</h2>
        {scans.length === 0 ? (
          <div className={`glass-card ${styles.emptyScans}`}>
            <div className="empty-state">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
              <h3>No scans yet</h3>
              <p style={{ fontSize: "0.8125rem", marginTop: 8 }}>
                Upload an agent and start a scan to see results here.
              </p>
              <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: 4 }}>
                Or visit the <Link href={`/workspace/${DEMO_WORKSPACE}/scan/demo/live`} style={{ color: "var(--accent)" }}>live demo</Link> to see the dashboard in action.
              </p>
            </div>
          </div>
        ) : (
          <div className={styles.scanList}>
            {scans.map((scan) => (
              <Link
                key={scan.id}
                href={`/workspace/${DEMO_WORKSPACE}/scan/${scan.id}/live`}
                className={`glass-card ${styles.scanCard}`}
              >
                <div className={styles.scanCardLeft}>
                  <code className={styles.scanId}>
                    {scan.id.slice(0, 8)}
                  </code>
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
                      ? scan.risk_score.toFixed(0)
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
