"""Unit tests for the GitHub Action's build-gate logic."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "action" / "sentinex_scan.py"
_spec = importlib.util.spec_from_file_location("sentinex_scan", _SCRIPT)
sentinex_scan = importlib.util.module_from_spec(_spec)
sys.modules["sentinex_scan"] = sentinex_scan
_spec.loader.exec_module(sentinex_scan)

evaluate_gate = sentinex_scan.evaluate_gate


def _finding(severity, rule_id="RULE-1"):
    return {"severity": severity, "rule_id": rule_id, "title": "t"}


def test_passes_with_no_findings():
    passed, reasons = evaluate_gate([], 0.0, "high", None)
    assert passed and reasons == []


def test_fails_on_severity_at_threshold():
    passed, reasons = evaluate_gate([_finding("high")], 10.0, "high", None)
    assert not passed
    assert "high" in reasons[0]


def test_fails_on_severity_above_threshold():
    passed, _ = evaluate_gate([_finding("critical")], 10.0, "high", None)
    assert not passed


def test_passes_when_below_threshold():
    passed, _ = evaluate_gate([_finding("medium")], 10.0, "high", None)
    assert passed


def test_never_disables_severity_gate():
    passed, _ = evaluate_gate([_finding("critical")], 10.0, "never", None)
    assert passed


def test_risk_score_gate():
    passed, reasons = evaluate_gate([], 80.0, "never", 50.0)
    assert not passed
    assert "exceeds" in reasons[0]
    passed, _ = evaluate_gate([], 50.0, "never", 50.0)
    assert passed


def test_score_gate_skipped_when_score_unknown():
    passed, _ = evaluate_gate([], None, "never", 50.0)
    assert passed


def test_invalid_severity_raises():
    with pytest.raises(ValueError):
        evaluate_gate([], 0.0, "catastrophic", None)


def test_grade_mapping():
    assert sentinex_scan._grade(None) == "?"
    assert sentinex_scan._grade(5) == "A+"
    assert sentinex_scan._grade(90) == "F"
