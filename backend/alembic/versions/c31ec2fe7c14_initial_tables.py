"""initial tables

Revision ID: c31ec2fe7c14
Revises:
Create Date: 2026-06-07 16:08:59.292686

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c31ec2fe7c14"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ───────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="candidate"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── resumes ─────────────────────────────────────────────
    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("blob_url", sa.String()),
        sa.Column("parsed_data", postgresql.JSON()),
        sa.Column("ats_score", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])

    # ── job_descriptions ────────────────────────────────────
    op.create_table(
        "job_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "recruiter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("parsed_data", postgresql.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_descriptions_recruiter_id", "job_descriptions", ["recruiter_id"])

    # ── interview_sessions ──────────────────────────────────
    op.create_table(
        "interview_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company", sa.String()),
        sa.Column("role", sa.String()),
        sa.Column("history", postgresql.JSON(), server_default="[]"),
        sa.Column("feedback", postgresql.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_interview_sessions_user_id", "interview_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_user_id", table_name="interview_sessions")
    op.drop_table("interview_sessions")
    op.drop_index("ix_job_descriptions_recruiter_id", table_name="job_descriptions")
    op.drop_table("job_descriptions")
    op.drop_index("ix_resumes_user_id", table_name="resumes")
    op.drop_table("resumes")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")