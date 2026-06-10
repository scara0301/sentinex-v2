"""Add scenario metadata columns missing from the initial schema.

The ORM model has had name/description/tags/builtin since Sprint 1, but
revision 0001 never created them — any code path touching scenarios
failed against a real database.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scenarios", sa.Column("name", sa.String(255), nullable=True))
    op.add_column("scenarios", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("scenarios", sa.Column("tags", sa.ARRAY(sa.String()), nullable=True))
    op.add_column(
        "scenarios",
        sa.Column(
            "builtin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("scenarios", "builtin")
    op.drop_column("scenarios", "tags")
    op.drop_column("scenarios", "description")
    op.drop_column("scenarios", "name")
