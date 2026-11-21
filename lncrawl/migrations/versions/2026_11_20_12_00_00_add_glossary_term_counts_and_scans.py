"""add per-chapter term counts and scan log

Revision ID: 3b7e5d1c9a26
Revises: 8f3a1c7d92b4
Create Date: 2026-11-20 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlmodel as sa
from sqlmodel.sql.sqltypes import AutoString

# revision identifiers, used by Alembic.
revision: str = "3b7e5d1c9a26"
down_revision: Union[str, Sequence[str], None] = "8f3a1c7d92b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

try:
    dialect = op.get_context().dialect.name
except Exception:
    dialect = ""


def upgrade() -> None:
    """Counts become per chapter, so rescanning a batch replaces them."""
    op.create_table(
        "glossary_term_counts",
        sa.Column("id", AutoString(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("term_id", AutoString(), nullable=False),
        sa.Column("chapter_id", AutoString(), nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["term_id"], ["glossary_terms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("term_id", "chapter_id"),
    )
    with op.batch_alter_table("glossary_term_counts", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_glossary_term_counts_chapter_id"), ["chapter_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_term_counts_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_term_counts_term_id"), ["term_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_term_counts_updated_at"), ["updated_at"], unique=False
        )

    op.create_table(
        "glossary_scans",
        sa.Column("id", AutoString(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("novel_id", AutoString(), nullable=False),
        sa.Column("first_chapter", sa.Integer(), nullable=False),
        sa.Column("requested", sa.Integer(), nullable=False),
        sa.Column("scanned", sa.Integer(), nullable=False),
        sa.Column("found", sa.Integer(), nullable=False),
        sa.Column("found_total", sa.Integer(), nullable=False),
        sa.Column("new_terms", sa.Integer(), nullable=False),
        sa.Column("updated_terms", sa.Integer(), nullable=False),
        sa.Column("cap", sa.Integer(), nullable=False),
        sa.Column("capped", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("glossary_scans", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_glossary_scan_lookup"), ["novel_id", "created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_glossary_scans_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_glossary_scans_novel_id"), ["novel_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_glossary_scans_updated_at"), ["updated_at"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("glossary_scans", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_glossary_scans_updated_at"))
        batch_op.drop_index(batch_op.f("ix_glossary_scans_novel_id"))
        batch_op.drop_index(batch_op.f("ix_glossary_scans_created_at"))
        batch_op.drop_index(batch_op.f("ix_glossary_scan_lookup"))

    op.drop_table("glossary_scans")

    with op.batch_alter_table("glossary_term_counts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_glossary_term_counts_updated_at"))
        batch_op.drop_index(batch_op.f("ix_glossary_term_counts_term_id"))
        batch_op.drop_index(batch_op.f("ix_glossary_term_counts_created_at"))
        batch_op.drop_index(batch_op.f("ix_glossary_term_counts_chapter_id"))

    op.drop_table("glossary_term_counts")
