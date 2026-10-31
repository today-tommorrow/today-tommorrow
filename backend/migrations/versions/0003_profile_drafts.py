"""Persist owner-scoped intake drafts independently of the education catalog."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_profile_drafts"
down_revision = "0002_auth_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intake_profiles",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "answers_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_items_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "completed_sections_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "slot_origins_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "unresolved_conflicts_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("submitted_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["today_app.users.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("status IN ('draft','submitted')", name="profile_status"),
        sa.CheckConstraint("revision >= 0", name="profile_revision"),
        sa.CheckConstraint(
            "submitted_revision IS NULL OR submitted_revision <= revision",
            name="profile_submitted_revision",
        ),
        schema="today_app",
    )
    op.create_index(
        "ix_today_app_intake_profiles_owner",
        "intake_profiles",
        ["user_id", "updated_at"],
        schema="today_app",
    )


def downgrade():
    op.drop_index(
        "ix_today_app_intake_profiles_owner",
        table_name="intake_profiles",
        schema="today_app",
    )
    op.drop_table("intake_profiles", schema="today_app")
