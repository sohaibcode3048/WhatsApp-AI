"""create_approvals_table

Revision ID: a1c3e5f7b901
Revises: b7420ba60ea1
Create Date: 2026-10-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3e5f7b901'
down_revision: Union[str, Sequence[str], None] = 'b7420ba60ea1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the approvals table for high-value operation authorisation."""
    op.create_table(
        'approvals',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('shop_id', sa.Uuid(), nullable=False),
        sa.Column('requested_by', sa.String(length=50), nullable=False),
        sa.Column('approved_by', sa.String(length=50), nullable=True),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_approvals_shop_id'), ['shop_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_approvals_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_approvals_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    """Drop the approvals table."""
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_approvals_created_at'))
        batch_op.drop_index(batch_op.f('ix_approvals_status'))
        batch_op.drop_index(batch_op.f('ix_approvals_shop_id'))

    op.drop_table('approvals')
