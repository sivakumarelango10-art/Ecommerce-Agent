"""Add lease_token to guest_email_outbox for worker lease fencing.

Revision ID: 0006_outbox_lease_token
Revises: 0005_guest_order_verification
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_outbox_lease_token"
down_revision: Union[str, None] = "0005_guest_order_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("guest_email_outbox", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("lease_token", sa.Uuid(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("guest_email_outbox", schema=None) as batch_op:
        batch_op.drop_column("lease_token")
