"""record what found a glossary term

Revision ID: 9c4f6a2e1b73
Revises: 8b3e5d1c7a92
Create Date: 2026-11-20 16:00:00.000000

The free scan and the paid LLM extractor both write terms. `found_by` says which
one first stored a surface (`local` or the model id), so the review sheet can
show them side by side and a comparison run can be scored per mode. Nullable, so
rows stored before this change stay unlabelled.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "9c4f6a2e1b73"
down_revision: Union[str, Sequence[str], None] = "8b3e5d1c7a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("glossary_terms", sa.Column("found_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("glossary_terms", "found_by")
