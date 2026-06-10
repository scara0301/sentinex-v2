"""
Scenario runner — turns scenario specs into proxy injection rules and
evaluates detections over the recorded event stream once a scan finishes.

The runner is deliberately stateless with respect to Docker/Redis: the
orchestrator feeds it plain event dicts (``{"seq", "type", "payload"}``)
loaded from the events table, and persists whatever findings come back.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Any, Optional

from ..findings import FindingDraft, honeypots
from .dsl import DetectionSpec, ScenarioSpec

MAX_EVIDENCE_SEQS = 25


class ScenarioRunner:
    def __init__(self, specs: list[ScenarioSpec]) -> None:
        self.specs = specs

    # ------------------------------------------------------------------
    # Proxy-side configuration
    # ------------------------------------------------------------------

    def injection_rules(self) -> list[dict[str, Any]]:
        """Serialize all injection rules for the proxy (stored in Redis)."""
        rules: list[dict[str, Any]] = []
        for spec in self.specs:
            for inj in spec.injections:
                rules.append(
                    {
                        "scenario": spec.slug,
                        "tool": inj.tool,
                        "mode": inj.mode,
                        "payload": inj.payload,
                        "max_hits": inj.max_hits,
                    }
                )
        return rules

    # ------------------------------------------------------------------
    # Post-run detection
    # ------------------------------------------------------------------

    def evaluate(self, events: list[dict[str, Any]]) -> list[FindingDraft]:
        """
        Evaluate every detection of every scenario against the event stream.

        ``events`` are dicts with at least ``seq``, ``type`` and ``payload``
        keys (payload itself a dict), ordered or unordered.
        """
        events = sorted(events, key=lambda e: e.get("seq", 0))
        first_injected = self._first_injected_seqs(events)

        drafts: list[FindingDraft] = []
        for spec in self.specs:
            injected_at = first_injected.get(spec.slug, first_injected.get(None))
            for det in spec.detections:
                matched = [
                    ev
                    for ev in events
                    if self._matches(det, ev, injected_at)
                ]
                if len(matched) >= det.match.min_count:
                    drafts.append(
                        FindingDraft(
                            rule_id=det.rule_id,
                            severity=det.severity,
                            category=det.category,
                            title=det.title,
                            cwe=list(det.cwe),
                            evidence={
                                "scenario": spec.slug,
                                "match_count": len(matched),
                                "event_seqs": [
                                    ev.get("seq", 0)
                                    for ev in matched[:MAX_EVIDENCE_SEQS]
                                ],
                            },
                        )
                    )
        return drafts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _first_injected_seqs(
        events: list[dict[str, Any]],
    ) -> dict[Optional[str], int]:
        """
        Map scenario slug -> seq of the first tool_result it poisoned.
        Key ``None`` holds the earliest injection across all scenarios.
        """
        firsts: dict[Optional[str], int] = {}
        for ev in events:
            if ev.get("type") != "tool_result":
                continue
            payload = ev.get("payload") or {}
            injected = payload.get("injected")
            if not injected:
                continue
            seq = ev.get("seq", 0)
            slug = injected if isinstance(injected, str) else None
            if slug not in firsts:
                firsts[slug] = seq
            if None not in firsts or seq < firsts[None]:
                firsts[None] = seq
        return firsts

    @staticmethod
    def _matches(
        det: DetectionSpec,
        event: dict[str, Any],
        injected_at: Optional[int],
    ) -> bool:
        m = det.match
        if event.get("type") != m.event:
            return False

        payload = event.get("payload") or {}

        if m.after_injection:
            if injected_at is None or event.get("seq", 0) <= injected_at:
                return False

        tool = payload.get("tool")
        if m.tool != "*" and (not tool or not fnmatch(str(tool), m.tool)):
            return False

        host = str(payload.get("host") or "")
        if m.host_contains and not any(s in host for s in m.host_contains):
            return False
        if m.host_not_contains and any(s in host for s in m.host_not_contains):
            return False

        needles = list(m.args_contain)
        if m.args_contain_honeypot:
            needles.extend(honeypots.ALL)
        if needles:
            blob = json.dumps(payload, default=str)
            if not any(n in blob for n in needles):
                return False

        return True
