"""Persist immutable question bindings, applied answers and idempotent responses."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_intake_questions"
down_revision = "0004_job_queue"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intake_questions",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("context_hash", sa.Text(), nullable=False),
        sa.Column("source_hashes_json", JSONB(), nullable=False),
        sa.Column("target_slot", sa.Text(), nullable=False),
        sa.Column("question_signature", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("draft_json", JSONB(), nullable=False),
        sa.Column("public_question_json", JSONB(), nullable=False),
        sa.Column("option_bindings_json", JSONB(), nullable=False),
        sa.Column("validation_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "profile_id", "user_id"],
            [
                "today_app.jobs.id",
                "today_app.jobs.profile_id",
                "today_app.jobs.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "profile_id", "user_id"),
        sa.UniqueConstraint("profile_id", "base_revision", "context_hash"),
        sa.CheckConstraint("base_revision >= 0", name="question_revision"),
        sa.CheckConstraint(
            "status IN ('ready','answered','stale')", name="question_status"
        ),
        sa.CheckConstraint(
            "origin IN ('llm','fallback') AND (origin='fallback' OR model_id IS NOT NULL)",
            name="question_origin",
        ),
        schema="today_app",
    )
    op.create_table(
        "intake_answers",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("question_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("selected_option_id", sa.Uuid(), nullable=False),
        sa.Column("applied_patch_json", JSONB(), nullable=False),
        sa.Column("revision_before", sa.Integer(), nullable=False),
        sa.Column("revision_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_id", "profile_id", "user_id"],
            [
                "today_app.intake_questions.id",
                "today_app.intake_questions.profile_id",
                "today_app.intake_questions.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revision_before >= 0 AND revision_after=revision_before+1",
            name="answer_revision",
        ),
        schema="today_app",
    )
    op.create_table(
        "intake_requests",
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("operation", sa.Text(), nullable=False, primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, primary_key=True),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("status_code IN (0,200,202)", name="intake_request_status"),
        schema="today_app",
    )
    op.create_index(
        "ix_questions_owner",
        "intake_questions",
        ["profile_id", "user_id", "base_revision"],
        schema="today_app",
    )
    op.create_index(
        "one_ready_intake_question",
        "intake_questions",
        ["profile_id"],
        unique=True,
        postgresql_where=sa.text("status='ready'"),
        schema="today_app",
    )
    op.create_index(
        "ix_today_app_intake_requests_profile_id",
        "intake_requests",
        ["profile_id"],
        schema="today_app",
    )


def downgrade():
    op.drop_index(
        "ix_today_app_intake_requests_profile_id",
        table_name="intake_requests",
        schema="today_app",
    )
    op.drop_table("intake_requests", schema="today_app")
    op.drop_table("intake_answers", schema="today_app")
    op.drop_index(
        "one_ready_intake_question", table_name="intake_questions", schema="today_app"
    )
    op.drop_index(
        "ix_questions_owner", table_name="intake_questions", schema="today_app"
    )
    op.drop_table("intake_questions", schema="today_app")
