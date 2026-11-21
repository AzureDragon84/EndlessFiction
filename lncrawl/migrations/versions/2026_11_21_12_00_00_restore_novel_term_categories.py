"""restore the translator's glossary term categories

Revision ID: c4f81a9d3e57
Revises: b7d19e4a6c52
Create Date: 2026-11-21 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f81a9d3e57"
down_revision: Union[str, Sequence[str], None] = "b7d19e4a6c52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Neutral member -> the member that carries it now. Enum columns store the
# name, so this is a data change, not a type change. The same map runs at boot
# for SQLite, which never replays a migration script.
RENAMES = {
    "place": "location",
    "organization": "sect_org",
    "title": "honorific",
    "rank": "rank_realm",
    "item": "artifact",
    "system": "special",
    "ship": "special",
    "event": "special",
    "other": "special",
}


def upgrade() -> None:
    """Rewrite rows written while the neutral set was the vocabulary."""
    for old, new in RENAMES.items():
        op.execute(f"UPDATE glossary_terms SET category = '{new}' WHERE category = '{old}'")


def downgrade() -> None:
    """Not reversible: `special` absorbed four neutral members."""
