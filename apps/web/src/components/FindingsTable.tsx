"use client";

import styles from "./FindingsTable.module.css";

interface FindingRow {
  id?: string;
  finding_id?: string;
  category: string;
  rule_id: string;
  severity: string;
  title: string;
}

interface FindingsTableProps {
  findings: FindingRow[];
}

export default function FindingsTable({ findings }: FindingsTableProps) {
  if (findings.length === 0) {
    return (
      <div className="empty-state" style={{ padding: "32px 16px" }}>
        <p style={{ fontSize: "0.8125rem" }}>No findings yet</p>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Rule</th>
            <th>Title</th>
            <th>Category</th>
          </tr>
        </thead>
        <tbody>
          {findings.map((f, i) => (
            <tr key={f.id ?? f.finding_id ?? i} className="animate-slide-in">
              <td>
                <span className={`badge badge-${f.severity}`}>
                  {f.severity.toUpperCase()}
                </span>
              </td>
              <td>
                <code className={styles.ruleId}>{f.rule_id}</code>
              </td>
              <td className={styles.title}>{f.title}</td>
              <td className={styles.category}>{f.category}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
