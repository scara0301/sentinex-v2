"""Drop workspaces.plan — the hosted-billing layer was removed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("workspaces", "plan")


def downgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("plan", sa.String(32), nullable=False, server_default="free"),
    )
