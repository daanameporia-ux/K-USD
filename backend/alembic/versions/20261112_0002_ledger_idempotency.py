"""ledger idempotency key for manual adjustments

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12 10:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ledger_entries', sa.Column('idempotency_key', sa.Text(), nullable=True))
    op.create_index('uq_ledger_user_idempotency', 'ledger_entries', ['user_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_ledger_user_idempotency', table_name='ledger_entries', postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    op.drop_column('ledger_entries', 'idempotency_key')
