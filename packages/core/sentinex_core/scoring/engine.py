"""
Streaming risk score computation.

The engine accumulates findings incrementally and recomputes the composite
risk score after each addition, enabling a live-updating gauge in the
dashboard.

Formula
-------
1. Each finding contributes: ``severity_weight × category_multiplier``
2. Base score = ``total_weight / finding_count``
3. Final score = ``min(100, base × SCALING_FACTOR)``
"""

from __future__ import annotations

from dataclasses import dataclass

SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 100.0,
    "high": 70.0,
    "medium": 40.0,
    "low": 10.0,
    "info": 0.0,
}

CATEGORY_MULTIPLIERS: dict[str, float] = {
    "llm_layer": 1.0,
    "tool_layer": 1.2,
    "memory_state": 0.9,
    "multi_agent": 1.1,
    "infrastructure": 1.5,
    "business_logic": 1.3,
}

SCALING_FACTOR: float = 1.5


@dataclass
class FindingEntry:
    severity: str
    category: str
    rule_id: str
    weight: float = 0.0

    def __post_init__(self):
        base = SEVERITY_WEIGHTS.get(self.severity, 0.0)
        mult = CATEGORY_MULTIPLIERS.get(self.category, 1.0)
        self.weight = base * mult


class RiskScoreEngine:
    """
    Streaming risk scorer.

    Call ``add_finding()`` for each new finding; it returns the updated
    composite score and the delta from the previous score.
    """

    def __init__(self) -> None:
        self._findings: list[FindingEntry] = []
        self._total_weight: float = 0.0
        self._current_score: float = 0.0

    def add_finding(
        self, severity: str, category: str, rule_id: str
    ) -> tuple[float, float]:
        """Add a finding and return ``(new_score, delta)``."""
        entry = FindingEntry(
            severity=severity, category=category, rule_id=rule_id
        )
        self._findings.append(entry)
        self._total_weight += entry.weight

        old_score = self._current_score
        self._current_score = self._compute()
        return self._current_score, self._current_score - old_score

    def current_score(self) -> float:
        return self._current_score

    @property
    def finding_count(self) -> int:
        return len(self._findings)

    @property
    def top_drivers(self) -> list[str]:
        """Return the top-5 rule_ids by weight contribution."""
        sorted_findings = sorted(
            self._findings, key=lambda f: f.weight, reverse=True
        )
        return [f.rule_id for f in sorted_findings[:5]]

    def _compute(self) -> float:
        if not self._findings:
            return 0.0
        mean_weight = self._total_weight / len(self._findings)
        return min(100.0, mean_weight * SCALING_FACTOR)

    @staticmethod
    def compute_from_findings(
        findings: list[tuple[str, str, str]],
    ) -> float:
        """
        One-shot computation from (severity, category, rule_id) tuples.
        Used by the orchestrator at the SCORING phase.
        """
        engine = RiskScoreEngine()
        for sev, cat, rule_id in findings:
            engine.add_finding(sev, cat, rule_id)
        return engine.current_score()
