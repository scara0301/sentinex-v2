"""Initial schema — all 9 tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- workspaces ---
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- agents ---
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("framework", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("bundle_uri", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "name", "version", name="uq_agents_workspace_name_version"),
    )
    op.create_index("ix_agents_workspace", "agents", ["workspace_id"])

    # --- tools ---
    op.create_table(
        "tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("side_effects", sa.ARRAY(sa.String()), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
    )

    # --- scenarios ---
    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("yaml", sa.Text(), nullable=True),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "workspace_id", "slug", "version", name="uq_scenarios_workspace_slug_version"
        ),
    )

    # --- scans ---
    op.create_table(
        "scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(64), nullable=False),
        sa.Column("scenario_ids", sa.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_scans_workspace_created", "scans", ["workspace_id", "created_at"])

    # --- events ---
    # NOTE: For high-volume deployments this table should be converted to a
    # partitioned table (PARTITION BY RANGE (ts) or PARTITION BY HASH (scan_id)).
    # Alembic does not natively emit PARTITION BY clauses; apply partitioning via
    # a manual migration using op.execute() with raw DDL after the initial deploy,
    # or use pg_partman for automated range partitioning.
    op.create_table(
        "events",
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("scan_id", "seq"),
    )
    op.create_index("ix_events_scan_ts", "events", ["scan_id", "ts"])

    # --- remediations (created before findings to allow the deferrable FK) ---
    op.create_table(
        "remediations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # finding_id FK is added after findings table exists; declared nullable here
        # and the FK constraint added below.
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("playbook_md", sa.Text(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rescan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # --- findings (references remediations via deferrable FK) ---
    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("rule_id", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cwe", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("remediation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        # Deferrable FK to remediations — allows inserting finding and remediation
        # in the same transaction without ordering concerns.
        sa.ForeignKeyConstraint(
            ["remediation_id"],
            ["remediations.id"],
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
            name="fk_findings_remediation_id_deferrable",
        ),
    )
    op.create_index("ix_findings_scan_severity", "findings", ["scan_id", "severity"])

    # Now add the FK from remediations.finding_id -> findings.id
    op.create_foreign_key(
        "fk_remediations_finding_id",
        "remediations",
        "findings",
        ["finding_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Also add rescan FK from remediations.rescan_id -> scans.id
    op.create_foreign_key(
        "fk_remediations_rescan_id",
        "remediations",
        "scans",
        ["rescan_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- badges ---
    op.create_table(
        "badges",
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("svg", sa.LargeBinary(), nullable=True),
        sa.Column("grade", sa.String(4), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("badges")
    op.drop_constraint("fk_remediations_rescan_id", "remediations", type_="foreignkey")
    op.drop_constraint("fk_remediations_finding_id", "remediations", type_="foreignkey")
    op.drop_index("ix_findings_scan_severity", table_name="findings")
    op.drop_table("findings")
    op.drop_table("remediations")
    op.drop_index("ix_events_scan_ts", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_scans_workspace_created", table_name="scans")
    op.drop_table("scans")
    op.drop_table("scenarios")
    op.drop_table("tools")
    op.drop_index("ix_agents_workspace", table_name="agents")
    op.drop_table("agents")
    op.drop_table("workspaces")
