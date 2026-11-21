"""remap glossary term categories to the language-neutral set

Revision ID: 5c9d2e8f1b47
Revises: 3b7e5d1c9a26
Create Date: 2026-11-20 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c9d2e8f1b47"
down_revision: Union[str, Sequence[str], None] = "3b7e5d1c9a26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Old member name -> the member that replaced it. Enum columns store the name,
# so this is a data change, not a type change. The same map runs at boot for
# SQLite, which never replays a migration script.
RENAMES = {
    "sect_org": "organization",
    "location": "place",
    "rank_realm": "rank",
    "artifact": "item",
    "special": "system",
    "bloodline": "system",
    "honorific": "title",
}


def upgrade() -> None:
    """Rewrite category rows written before the language-neutral set."""
    for old, new in RENAMES.items():
        op.execute(f"UPDATE glossary_terms SET category = '{new}' WHERE category = '{old}'")


def downgrade() -> None:
    """Not reversible: `system` and `item` each absorbed several old members."""
