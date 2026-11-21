"""rename glossary term pinyin to reading

Revision ID: b7d19e4a6c52
Revises: 9c4f6a2e1b73
Create Date: 2026-11-20 17:00:00.000000

The column now holds whatever the source language's profile can produce —
pinyin, katakana romaji or Revised Romanization — so the name had to stop
claiming it is always Chinese. No data changes: the values stay as stored, and
a profile that has no reading simply leaves the column empty.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d19e4a6c52"
down_revision: Union[str, Sequence[str], None] = "9c4f6a2e1b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("glossary_terms") as batch:
        batch.alter_column("pinyin", new_column_name="reading")


def downgrade() -> None:
    with op.batch_alter_table("glossary_terms") as batch:
        batch.alter_column("reading", new_column_name="pinyin")
