"""
Unit coverage for the finding-review request schema.

The review endpoint itself (``POST .../findings/{id}/review``) is a thin
FastAPI route over ``FindingRepo`` and requires a live Postgres instance —
consistent with this repo's convention, DB-backed behavior belongs in
``tests/integration/`` (see ``tests/conftest.py::test_db_url``), not here.
This file only exercises the pure-Python request validation.
"""

import pytest
from pydantic import ValidationError

from sentinex_api.routes.scans import FindingReviewRequest


def test_accepts_valid_statuses():
    for status in ("open", "confirmed", "dismissed"):
        req = FindingReviewRequest(status=status)
        assert req.status == status
        assert req.reviewer is None
        assert req.note is None


def test_accepts_optional_reviewer_and_note():
    req = FindingReviewRequest(status="dismissed", reviewer="alice", note="false positive")
    assert req.reviewer == "alice"
    assert req.note == "false positive"


def test_rejects_unknown_status():
    with pytest.raises(ValidationError):
        FindingReviewRequest(status="bogus")
