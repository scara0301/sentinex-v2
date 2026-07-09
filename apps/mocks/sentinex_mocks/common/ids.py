"""Realistic mixed-case object-ID generation for mock provider responses."""
from __future__ import annotations

import random
import string

_ALNUM = string.ascii_letters + string.digits


def mixed_case_id(prefix: str, length: int = 14) -> str:
    """Stripe-shaped id, e.g. ``cus_NffrFeUfNV2Hib`` — mixed-case, not
    lowercase-only hex like ``uuid4().hex``, which is a structural tell
    against real provider IDs."""
    return f"{prefix}_{''.join(random.choices(_ALNUM, k=length))}"
