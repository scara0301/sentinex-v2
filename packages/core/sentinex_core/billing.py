"""
SaaS billing primitives (Sprint 5): plans, limits, and usage accounting.

Payment-provider integration (Stripe checkout + webhooks) terminates at
``POST /workspace/{id}/plan`` — the webhook handler's job is to call that
endpoint (or the repo method behind it) when a subscription changes.
Everything else — metering and enforcement — lives here and is
provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class PlanLimits:
    name: str
    scans_per_month: int
    max_agents: int
    max_concurrent_scans: int
    custom_scenarios: bool
    price_usd_month: int


PLANS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        name="free",
        scans_per_month=10,
        max_agents=3,
        max_concurrent_scans=1,
        custom_scenarios=False,
        price_usd_month=0,
    ),
    "pro": PlanLimits(
        name="pro",
        scans_per_month=200,
        max_agents=25,
        max_concurrent_scans=5,
        custom_scenarios=True,
        price_usd_month=99,
    ),
    "enterprise": PlanLimits(
        name="enterprise",
        scans_per_month=10_000,
        max_agents=1_000,
        max_concurrent_scans=50,
        custom_scenarios=True,
        price_usd_month=999,
    ),
}

DEFAULT_PLAN = "free"

# Scan statuses that count against the concurrency limit.
ACTIVE_SCAN_STATUSES = (
    "PENDING",
    "PROVISIONING",
    "SEEDING",
    "RUNNING",
    "DRAINING",
    "SCORING",
    "REPORTING",
    "PAUSED",
)


def get_plan(name: str | None) -> PlanLimits:
    """Resolve a plan by name, falling back to the free tier."""
    return PLANS.get(name or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def current_period_start(now: datetime | None = None) -> datetime:
    """Start of the current monthly metering period (calendar month, UTC)."""
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
