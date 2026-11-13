"""staff review of payment screenshots

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('orders', sa.Column('proof_reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('proof_reviewed_by', sa.BigInteger(), nullable=True))
    op.create_foreign_key(op.f('fk_orders_proof_reviewed_by_staff'), 'orders', 'staff', ['proof_reviewed_by'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_orders_proof_reviewed_by_staff'), 'orders', type_='foreignkey')
    op.drop_column('orders', 'proof_reviewed_by')
    op.drop_column('orders', 'proof_reviewed_at')
