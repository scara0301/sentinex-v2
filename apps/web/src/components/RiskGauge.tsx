"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./RiskGauge.module.css";

interface RiskGaugeProps {
  score: number;
  delta?: number;
  drivers?: string[];
  findingCount?: number;
}

function scoreColor(score: number): string {
  if (score >= 75) return "#ef4444";
  if (score >= 50) return "#f97316";
  if (score >= 25) return "#eab308";
  if (score > 0) return "#3b82f6";
  return "#34d399";
}

function scoreLabel(score: number): string {
  if (score >= 75) return "CRITICAL";
  if (score >= 50) return "HIGH";
  if (score >= 25) return "MEDIUM";
  if (score > 0) return "LOW";
  return "CLEAN";
}

export default function RiskGauge({
  score,
  delta = 0,
  drivers = [],
  findingCount = 0,
}: RiskGaugeProps) {
  const [displayScore, setDisplayScore] = useState(0);
  const [isPulsing, setIsPulsing] = useState(false);
  const prevScoreRef = useRef(0);

  useEffect(() => {
    const start = prevScoreRef.current;
    const end = score;
    const duration = 600;
    const startTime = performance.now();
    let rafId: number;

    const animate = (now: number) => {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplayScore(start + (end - start) * eased);
      if (progress < 1) {
        rafId = requestAnimationFrame(animate);
      }
    };

    rafId = requestAnimationFrame(animate);
    prevScoreRef.current = end;

    if (delta !== 0) {
      setIsPulsing(true);
      const t = setTimeout(() => setIsPulsing(false), 600);
      return () => {
        cancelAnimationFrame(rafId);
        clearTimeout(t);
      };
    }
    return () => cancelAnimationFrame(rafId);
  }, [score, delta]);

  const radius = 80;
  const stroke = 10;
  const circumference = 2 * Math.PI * radius;
  const arc = circumference * 0.75; // 270deg arc
  const offset = arc - (displayScore / 100) * arc;
  const color = scoreColor(displayScore);

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <h3>Risk Score</h3>
        {findingCount > 0 && (
          <span className={styles.findingCount}>{findingCount} findings</span>
        )}
      </div>

      <div className={styles.gaugeWrap}>
        <svg
          className={`${styles.gauge} ${isPulsing ? styles.pulse : ""}`}
          viewBox="0 0 200 200"
          width="200"
          height="200"
        >
          <circle
            cx="100"
            cy="100"
            r={radius}
            fill="none"
            stroke="var(--gauge-track)"
            strokeWidth={stroke}
            strokeDasharray={`${arc} ${circumference}`}
            strokeDashoffset="0"
            strokeLinecap="round"
            transform="rotate(135, 100, 100)"
          />
          {/* Filled arc */}
          <circle
            cx="100"
            cy="100"
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeDasharray={`${arc} ${circumference}`}
            strokeDashoffset={offset}
            strokeLinecap="round"
            transform="rotate(135, 100, 100)"
            style={{
              transition: "stroke-dashoffset 0.6s cubic-bezier(0.4, 0, 0.2, 1), stroke 0.3s ease",
              filter: `drop-shadow(0 0 8px ${color}40)`,
            }}
          />
          <text
            x="100"
            y="92"
            textAnchor="middle"
            className={styles.scoreText}
            fill={color}
          >
            {displayScore.toFixed(0)}
          </text>
          <text
            x="100"
            y="115"
            textAnchor="middle"
            className={styles.labelText}
            fill="var(--text-muted)"
          >
            {scoreLabel(displayScore)}
          </text>
        </svg>

        {delta !== 0 && (
          <div
            className={styles.delta}
            style={{ color: delta > 0 ? "#ef4444" : "#34d399" }}
          >
            {delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
          </div>
        )}
      </div>

      {drivers.length > 0 && (
        <div className={styles.drivers}>
          <span className={styles.driversLabel}>Top drivers</span>
          <div className={styles.driversList}>
            {drivers.slice(0, 3).map((d) => (
              <code key={d} className={styles.driverTag}>
                {d}
              </code>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
