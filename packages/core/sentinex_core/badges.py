"""
Embeddable SVG scan badges (Sprint 4).

A badge shows the letter grade derived from a scan's 0–100 risk score and
is signed with HMAC-SHA256 so third parties can verify it was issued by
this SENTINEX instance.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from typing import Optional

# (max_score, grade) — evaluated in order. None scores render as "?".
_GRADE_BANDS: list[tuple[float, str]] = [
    (10.0, "A+"),
    (25.0, "A"),
    (45.0, "B"),
    (65.0, "C"),
    (85.0, "D"),
    (100.0, "F"),
]

_GRADE_COLORS: dict[str, str] = {
    "A+": "#4c1",
    "A": "#4c1",
    "B": "#97ca00",
    "C": "#dfb317",
    "D": "#fe7d37",
    "F": "#e05d44",
    "?": "#9f9f9f",
}


def grade_for_score(score: Optional[float]) -> str:
    if score is None:
        return "?"
    for max_score, grade in _GRADE_BANDS:
        if score <= max_score:
            return grade
    return "F"


def render_badge_svg(
    grade: str,
    score: Optional[float] = None,
    label: str = "sentinex",
) -> str:
    """Render a shields.io-style flat badge as an SVG string."""
    value = grade if score is None else f"{grade} ({score:.0f})"
    color = _GRADE_COLORS.get(grade, _GRADE_COLORS["?"])

    char_w = 6.5
    pad = 10
    label_w = int(len(label) * char_w + pad * 2)
    value_w = int(len(value) * char_w + pad * 2)
    total_w = label_w + value_w

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total_w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w}" height="20" fill="#555"/>
    <rect x="{label_w}" width="{value_w}" height="20" fill="{color}"/>
    <rect width="{total_w}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_w / 2}" y="14">{label}</text>
    <text x="{label_w + value_w / 2}" y="14" font-weight="bold">{value}</text>
  </g>
</svg>
"""


def sign_badge(
    secret: str, scan_id: str, grade: str, signed_at: datetime
) -> str:
    """HMAC-SHA256 signature over the badge's identity tuple."""
    message = f"{scan_id}:{grade}:{signed_at.isoformat()}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_badge(
    secret: str, scan_id: str, grade: str, signed_at: datetime, signature: str
) -> bool:
    expected = sign_badge(secret, scan_id, grade, signed_at)
    return hmac.compare_digest(expected, signature)
