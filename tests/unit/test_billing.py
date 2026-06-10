from datetime import datetime, timezone

from sentinex_core.billing import (
    ACTIVE_SCAN_STATUSES,
    DEFAULT_PLAN,
    PLANS,
    current_period_start,
    get_plan,
)


def test_default_plan_is_free():
    assert get_plan(None).name == DEFAULT_PLAN
    assert get_plan("nonsense").name == DEFAULT_PLAN


def test_plan_tiers_are_ordered():
    free, pro, ent = PLANS["free"], PLANS["pro"], PLANS["enterprise"]
    assert free.scans_per_month < pro.scans_per_month < ent.scans_per_month
    assert free.max_agents < pro.max_agents < ent.max_agents
    assert free.max_concurrent_scans < pro.max_concurrent_scans
    assert not free.custom_scenarios
    assert pro.custom_scenarios


def test_current_period_start_is_first_of_month():
    now = datetime(2026, 6, 10, 15, 30, tzinfo=timezone.utc)
    start = current_period_start(now)
    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_terminal_statuses_not_active():
    assert "DONE" not in ACTIVE_SCAN_STATUSES
    assert "FAILED" not in ACTIVE_SCAN_STATUSES
    assert "RUNNING" in ACTIVE_SCAN_STATUSES
    assert "PAUSED" in ACTIVE_SCAN_STATUSES
