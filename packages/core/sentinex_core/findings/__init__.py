"""Vulnerability finding primitives shared by the scenario runner and detectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import honeypots

__all__ = ["FindingDraft", "honeypots"]


@dataclass
class FindingDraft:
    """An in-memory finding produced by a detector, prior to persistence."""

    rule_id: str
    severity: str
    category: str
    title: str
    cwe: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: str = "strong"
