"""save the sources of an interview's opening question

Revision ID: e5b1c7a49d20
Revises: c31ec2fe7c14
Create Date: 2026-11-24 18:00:00.000000

Adds two nullable columns to interview_sessions: the reviewed pages the opening
question was based on, and the note shown when none matched. Rows that already
exist keep NULL in both, which the API reads as "no sources, no notice".
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e5b1c7a49d20"
down_revision: Union[str, None] = "c31ec2fe7c14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column("opening_sources", postgresql.JSON(), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("opening_notice", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "opening_notice")
    op.drop_column("interview_sessions", "opening_sources")
