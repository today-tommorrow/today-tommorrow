"""Owner-bound recommendation jobs and immutable input/result snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_recommendation_runs"
down_revision = "0006_answer_history_index"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("job_kind", "jobs", schema="today_app", type_="check")
    op.create_check_constraint(
        "job_kind",
        "jobs",
        "kind IN ('intake_question','recommendation')",
        schema="today_app",
    )
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("input_json", JSONB(), nullable=False),
        sa.Column("public_json", JSONB(), nullable=False),
        sa.Column("provenance_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id", "profile_id", "user_id"],
            [
                "today_app.jobs.id",
                "today_app.jobs.profile_id",
                "today_app.jobs.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("profile_revision >= 0", name="recommendation_revision"),
        schema="today_app",
    )
    op.create_index(
        "ix_recommendation_runs_owner",
        "recommendation_runs",
        ["user_id", "created_at"],
        schema="today_app",
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM today_app.jobs WHERE kind='recommendation')"
        )
    ):
        raise RuntimeError("recommendation_jobs_require_explicit_retention_decision")
    op.drop_index(
        "ix_recommendation_runs_owner",
        table_name="recommendation_runs",
        schema="today_app",
    )
    op.drop_table("recommendation_runs", schema="today_app")
    op.drop_constraint("job_kind", "jobs", schema="today_app", type_="check")
    op.create_check_constraint(
        "job_kind", "jobs", "kind = 'intake_question'", schema="today_app"
    )
