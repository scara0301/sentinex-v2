"""
Streaming risk score computation.

The engine accumulates findings incrementally and recomputes the composite
risk score after each addition, enabling a live-updating gauge in the
dashboard.

Formula
-------
1. Each finding contributes a hazard ``p = severity_weight ×
   category_multiplier / MAX_SINGLE_WEIGHT`` in ``[0, 1]``.
2. Hazards combine with a "noisy-OR": ``risk = 1 − ∏(1 − p_i)``.
3. Final score = ``100 × risk``.

Because every ``(1 − p_i)`` factor is in ``[0, 1]``, the product can only
shrink as findings are added, so the score is **monotonically
non-decreasing**: discovering another vulnerability never lowers the
reported risk (and the per-finding ``delta`` is never negative). An
earlier revision averaged the weights, which let a handful of low-severity
findings *drag down* a score already driven high by a critical one.
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

# Largest weight any single finding can contribute (critical severity ×
# the highest category multiplier). Used to normalize a finding's weight
# into a [0, 1] hazard, so one maximal finding alone pins the score at 100.
MAX_SINGLE_WEIGHT: float = max(SEVERITY_WEIGHTS.values()) * max(
    CATEGORY_MULTIPLIERS.values()
)


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
        # Running product of the (1 - hazard) survival factors. Starts at 1.0
        # (no risk) and only ever shrinks as findings accumulate.
        self._survival: float = 1.0
        self._current_score: float = 0.0

    def add_finding(
        self, severity: str, category: str, rule_id: str
    ) -> tuple[float, float]:
        """Add a finding and return ``(new_score, delta)``. ``delta >= 0``."""
        entry = FindingEntry(
            severity=severity, category=category, rule_id=rule_id
        )
        self._findings.append(entry)
        hazard = min(1.0, entry.weight / MAX_SINGLE_WEIGHT) if MAX_SINGLE_WEIGHT else 0.0
        self._survival *= 1.0 - hazard

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
        return min(100.0, 100.0 * (1.0 - self._survival))

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
