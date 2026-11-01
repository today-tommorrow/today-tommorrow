"""Persist owner-scoped question jobs with fenced execution leases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_job_queue"
down_revision = "0003_profile_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("phase", sa.Text(), server_default="queued", nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "profile_id", "user_id"),
        sa.UniqueConstraint("user_id", "kind", "idempotency_key"),
        sa.CheckConstraint("kind = 'intake_question'", name="job_kind"),
        sa.CheckConstraint("document_id IS NULL", name="job_document"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','superseded','cancelled')",
            name="job_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="job_attempt"),
        sa.CheckConstraint("profile_revision >= 0", name="job_profile_revision"),
        schema="today_app",
    )
    op.create_index(
        "ix_today_app_jobs_owner", "jobs", ["user_id", "created_at"], schema="today_app"
    )


def downgrade():
    op.drop_index("ix_today_app_jobs_owner", table_name="jobs", schema="today_app")
    op.drop_table("jobs", schema="today_app")
