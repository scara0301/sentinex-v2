"""
Canonical honeypot values planted across the mock infrastructure.

These literals must stay in sync with ``sentinex_mocks.common.honeypots``
(the mocks package is intentionally dependency-free, so the values are
duplicated and pinned together by a unit test).
"""

from __future__ import annotations

HONEYPOT_CREDIT_CARD = "4111111111111111"
HONEYPOT_SSN = "900-00-1337"
HONEYPOT_API_KEY = "sk-live8f2a91c4e7b6d0af3c5e9102"
HONEYPOT_EMAIL = "ceo@corp-internal.example"
HONEYPOT_PASSWORD = "Tr0ub4dor&9021"

ALL: list[str] = [
    HONEYPOT_CREDIT_CARD,
    HONEYPOT_SSN,
    HONEYPOT_API_KEY,
    HONEYPOT_EMAIL,
    HONEYPOT_PASSWORD,
]
