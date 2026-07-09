"""Add confidence and review-status columns to findings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("confidence", sa.String(16), nullable=False, server_default="strong"),
    )
    op.add_column(
        "findings",
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
    )
    op.add_column(
        "findings", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "findings", sa.Column("reviewed_by", sa.String(255), nullable=True)
    )
    op.add_column(
        "findings", sa.Column("review_note", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_findings_scan_status_severity",
        "findings",
        ["scan_id", "status", "severity"],
    )


def downgrade() -> None:
    op.drop_index("ix_findings_scan_status_severity", table_name="findings")
    op.drop_column("findings", "review_note")
    op.drop_column("findings", "reviewed_by")
    op.drop_column("findings", "reviewed_at")
    op.drop_column("findings", "status")
    op.drop_column("findings", "confidence")
