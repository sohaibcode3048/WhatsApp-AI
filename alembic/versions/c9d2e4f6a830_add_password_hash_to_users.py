"""add password_hash to users

Revision ID: c9d2e4f6a830
Revises: a1c3e5f7b901
Create Date: 2026-10-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision: str = "c9d2e4f6a830"
down_revision: Union[str, None] = "a1c3e5f7b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
