"use client";

import { useCallback, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

import { useScanWS, type WSEvent } from "@/lib/ws";
import EventStream from "@/components/EventStream";
import RiskGauge from "@/components/RiskGauge";
import ScanStatusBadge from "@/components/ScanStatusBadge";
import FindingsTable from "@/components/FindingsTable";

import styles from "./page.module.css";

interface FindingEntry {
  finding_id: string;
  category: string;
  rule_id: string;
  severity: string;
  title: string;
}

export default function ScanLivePage() {
  const params = useParams();
  const workspaceId = params.id as string;
  const scanId = params.sid as string;

  if (!workspaceId || !scanId) {
    return <main className="page-container"><p>Invalid scan URL.</p></main>;
  }

  const [events, setEvents] = useState<WSEvent[]>([]);
  const [scanStatus, setScanStatus] = useState("PENDING");
  const [riskScore, setRiskScore] = useState(0);
  const [riskDelta, setRiskDelta] = useState(0);
  const [riskDrivers, setRiskDrivers] = useState<string[]>([]);
  const [findings, setFindings] = useState<FindingEntry[]>([]);

  const handleEvent = useCallback((event: WSEvent) => {
    setEvents((prev) => [...prev, event]);

    switch (event.type) {
      case "state": {
        const to = event.payload.to as string;
        if (to) setScanStatus(to);
        break;
      }
      case "risk_update": {
        setRiskScore(event.payload.score as number);
        setRiskDelta(event.payload.delta as number);
        setRiskDrivers((event.payload.drivers as string[]) ?? []);
        break;
      }
      case "finding": {
        const f: FindingEntry = {
          finding_id: event.payload.finding_id as string,
          category: event.payload.category as string,
          rule_id: event.payload.rule_id as string,
          severity: event.payload.severity as string,
          title: event.payload.title as string,
        };
        setFindings((prev) => [...prev, f]);
        break;
      }
    }
  }, []);

  const { connectionState } = useScanWS({
    workspaceId,
    scanId,
    onEvent: handleEvent,
  });

  return (
    <main className="page-container">
      <div className={styles.pageHeader}>
        <div className={styles.headerLeft}>
          <Link href="/" className={styles.backLink}>
            ← Dashboard
          </Link>
          <h1 className={styles.title}>
            Scan <code className={styles.scanId}>{scanId.slice(0, 8)}</code>
          </h1>
          <ScanStatusBadge status={scanStatus} />
        </div>
        <div className={styles.headerRight}>
          <div className={styles.connectionStatus}>
            <span
              className={`connection-dot ${connectionState}`}
              title={connectionState}
            />
            <span className={styles.connectionLabel}>{connectionState}</span>
          </div>
        </div>
      </div>

      <div className="grid-dashboard">
        <div className={`glass-card ${styles.streamPanel}`}>
          <EventStream events={events} />
        </div>

        <div className={styles.sidebar}>
          <div className={`glass-card ${styles.gaugeCard}`}>
            <RiskGauge
              score={riskScore}
              delta={riskDelta}
              drivers={riskDrivers}
              findingCount={findings.length}
            />
          </div>

          <div className={`glass-card ${styles.findingsCard}`}>
            <div className={styles.findingsHeader}>
              <h3>Findings</h3>
              <span className={styles.findingCount}>
                {findings.length}
              </span>
            </div>
            <FindingsTable findings={findings} />
          </div>
        </div>
      </div>
    </main>
  );
}
