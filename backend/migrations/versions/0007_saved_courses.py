"""Store account-owned course saves independently of mutable source tables."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_saved_courses"
down_revision = "0006_answer_history_index"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("saved_courses",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("source_course_id", sa.Text(), nullable=False),
        sa.Column("saved_catalog_version_id", sa.Uuid(), nullable=False),
        sa.Column("saved_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "course_id"),
        sa.ForeignKeyConstraint(["user_id"], ["today_app.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "source_course_id", name="uq_saved_source_owner"),
        schema="today_app")
    op.create_index("ix_saved_courses_owner_order", "saved_courses", ["user_id", "created_at", "course_id"], schema="today_app")


def downgrade():
    op.drop_index("ix_saved_courses_owner_order", table_name="saved_courses", schema="today_app")
    op.drop_table("saved_courses", schema="today_app")
