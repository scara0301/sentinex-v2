from datetime import datetime, timezone

from sentinex_core.badges import (
    grade_for_score,
    render_badge_svg,
    sign_badge,
    verify_badge,
)


def test_grade_bands():
    assert grade_for_score(None) == "?"
    assert grade_for_score(0) == "A+"
    assert grade_for_score(10) == "A+"
    assert grade_for_score(25) == "A"
    assert grade_for_score(45) == "B"
    assert grade_for_score(65) == "C"
    assert grade_for_score(85) == "D"
    assert grade_for_score(100) == "F"


def test_render_badge_svg():
    svg = render_badge_svg("A", score=12.0)
    assert svg.startswith("<svg")
    assert "A (12)" in svg
    assert "sentinex" in svg


def test_signature_roundtrip():
    signed_at = datetime(2026, 6, 10, tzinfo=timezone.utc)
    sig = sign_badge("secret", "scan-1", "A", signed_at)
    assert verify_badge("secret", "scan-1", "A", signed_at, sig)
    assert not verify_badge("other-secret", "scan-1", "A", signed_at, sig)
    assert not verify_badge("secret", "scan-1", "B", signed_at, sig)
