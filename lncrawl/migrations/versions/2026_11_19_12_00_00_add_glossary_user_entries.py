"""add per-reader glossary overrides

Revision ID: 8f3a1c7d92b4
Revises: 12cc02633915
Create Date: 2026-11-19 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlmodel as sa
from sqlmodel.sql.sqltypes import AutoString

# revision identifiers, used by Alembic.
revision: str = "8f3a1c7d92b4"
down_revision: Union[str, Sequence[str], None] = "12cc02633915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

try:
    dialect = op.get_context().dialect.name
except Exception:
    dialect = ""


def upgrade() -> None:
    """A reader's personal rendering never changes the admin decision."""
    op.create_table(
        "glossary_user_entries",
        sa.Column("id", AutoString(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("user_id", AutoString(), nullable=False),
        sa.Column("term_id", AutoString(), nullable=False),
        sa.Column("language", AutoString(), nullable=False),
        sa.Column("rendering", AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["term_id"], ["glossary_terms.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "term_id", "language"),
    )
    with op.batch_alter_table("glossary_user_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_glossary_user_entries_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_user_entries_updated_at"), ["updated_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_user_entries_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("glossary_user_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_glossary_user_entries_user_id"))
        batch_op.drop_index(batch_op.f("ix_glossary_user_entries_updated_at"))
        batch_op.drop_index(batch_op.f("ix_glossary_user_entries_created_at"))

    op.drop_table("glossary_user_entries")
