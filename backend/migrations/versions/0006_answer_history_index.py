"""Index the owner-scoped answer history read on every profile re-entry."""

from alembic import op

revision = "0006_answer_history_index"
down_revision = "0005_intake_questions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_intake_answers_owner_revision",
        "intake_answers",
        ["profile_id", "user_id", "revision_after"],
        schema="today_app",
    )


def downgrade():
    op.drop_index(
        "ix_intake_answers_owner_revision",
        table_name="intake_answers",
        schema="today_app",
    )
