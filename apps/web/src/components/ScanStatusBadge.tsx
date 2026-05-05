/**
 * ScanStatusBadge — animated state badge for the scan lifecycle.
 */
"use client";

import styles from "./ScanStatusBadge.module.css";

interface ScanStatusBadgeProps {
  status: string;
  size?: "sm" | "md";
}

const STATUS_STYLES: Record<string, string> = {
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

const STATUS_ICONS: Record<string, string> = {
  PENDING: "◯",
  PROVISIONING: "⟳",
  SEEDING: "⟳",
  RUNNING: "▶",
  DRAINING: "⟳",
  SCORING: "◈",
  REPORTING: "◈",
  DONE: "✓",
  FAILED: "✗",
  PAUSED: "⏸",
};

export default function ScanStatusBadge({
  status,
  size = "md",
}: ScanStatusBadgeProps) {
  const badgeClass = STATUS_STYLES[status] ?? "badge-pending";
  const icon = STATUS_ICONS[status] ?? "?";

  return (
    <span
      className={`badge ${badgeClass} ${size === "sm" ? styles.sm : ""}`}
      title={status}
    >
      <span className={styles.icon}>{icon}</span>
      {status}
    </span>
  );
}
