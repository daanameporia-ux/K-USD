"""address book: saved withdrawal addresses

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'saved_addresses',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('network', postgresql.ENUM('TRC20', 'BEP20', name='network', create_type=False), nullable=False),
        sa.Column('address', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), server_default='', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_saved_addresses_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_addresses')),
        sa.UniqueConstraint('user_id', 'network', 'address', name='uq_saved_addresses_user_address'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('saved_addresses')
