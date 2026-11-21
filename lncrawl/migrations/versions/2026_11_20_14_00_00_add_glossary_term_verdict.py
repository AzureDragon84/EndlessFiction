"""add a review verdict to glossary terms

Revision ID: 7f1a4c9d2b58
Revises: 5c9d2e8f1b47
Create Date: 2026-11-20 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "7f1a4c9d2b58"
down_revision: Union[str, Sequence[str], None] = "5c9d2e8f1b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enum columns store the member name, so the column only needs the same width
# and nullability the model declares. Null means "no verdict yet", which is why
# existing rows need no rewrite.
VERDICTS = ("keep", "skip", "fragment", "merge")


def upgrade() -> None:
    op.add_column(
        "glossary_terms",
        sa.Column(
            "verdict",
            sa.Enum(*VERDICTS, name="termverdict", native_enum=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("glossary_terms", "verdict")
