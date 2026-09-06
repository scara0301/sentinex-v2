"""Add scans.worker_id so scan ownership is attributable to a worker process.

Startup reaping and stale-scan failure previously applied to every scan on
the host, so bringing one worker up marked its peers' running scans FAILED
and force-removed their sandbox containers. Recording the owning worker lets
both operations be scoped correctly.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("worker_id", sa.String(128), nullable=True))
    op.create_index("ix_scans_worker_status", "scans", ["worker_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_scans_worker_status", table_name="scans")
    op.drop_column("scans", "worker_id")
