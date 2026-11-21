"""record which model judged a glossary term, and when

Revision ID: 8b3e5d1c7a92
Revises: 7f1a4c9d2b58
Create Date: 2026-11-20 15:00:00.000000

The verdict column says what review decided; these two say who decided it when
the decision came from the paid judge rather than a human, so a bad batch can be
found and re-run. Both are nullable, so existing rows stay undecided.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "8b3e5d1c7a92"
down_revision: Union[str, Sequence[str], None] = "7f1a4c9d2b58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("glossary_terms", sa.Column("judged_by", sa.String(), nullable=True))
    op.add_column("glossary_terms", sa.Column("judged_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("glossary_terms", "judged_at")
    op.drop_column("glossary_terms", "judged_by")
