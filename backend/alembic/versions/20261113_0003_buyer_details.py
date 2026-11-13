"""buyer details on orders: SBP phone, full name, payment screenshot

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROOF_MISSING = "status = 'paid' AND proof_required AND proof_uploaded_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""
    for name in ('require_buyer_phone', 'require_buyer_name', 'require_payment_proof'):
        op.add_column('users', sa.Column(name, sa.Boolean(), server_default='true', nullable=False))
    op.add_column('orders', sa.Column('buyer_phone', sa.Text(), nullable=True))
    op.add_column('orders', sa.Column('buyer_name', sa.Text(), nullable=True))
    op.add_column('orders', sa.Column('proof_required', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('orders', sa.Column('proof_uploaded_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_orders_proof_missing', 'orders', ['user_id'], postgresql_where=sa.text(PROOF_MISSING))
    op.create_table(
        'order_proofs',
        sa.Column('order_id', sa.BigInteger(), nullable=False),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('content_type', sa.Text(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_proofs_order_id_orders')),
        sa.PrimaryKeyConstraint('order_id', name=op.f('pk_order_proofs')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('order_proofs')
    op.drop_index('ix_orders_proof_missing', table_name='orders', postgresql_where=sa.text(PROOF_MISSING))
    for name in ('proof_uploaded_at', 'proof_required', 'buyer_name', 'buyer_phone'):
        op.drop_column('orders', name)
    for name in ('require_payment_proof', 'require_buyer_name', 'require_buyer_phone'):
        op.drop_column('users', name)
