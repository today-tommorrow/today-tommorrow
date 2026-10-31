"""Store service users, sessions and one-time Google login state in today_app."""

import sqlalchemy as sa
from alembic import op

revision = "0002_auth_storage"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("google_sub"),
        schema="today_app",
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["today_app.users.id"], ondelete="CASCADE"),
        schema="today_app",
    )
    op.create_index("ix_today_app_auth_sessions_user_id", "auth_sessions", ["user_id"], schema="today_app")
    op.create_table(
        "oauth_login_states",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("browser_binding_hash", sa.Text(), nullable=False),
        sa.Column("nonce_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
        schema="today_app",
    )


def downgrade():
    op.drop_table("oauth_login_states", schema="today_app")
    op.drop_index("ix_today_app_auth_sessions_user_id", table_name="auth_sessions", schema="today_app")
    op.drop_table("auth_sessions", schema="today_app")
    op.drop_table("users", schema="today_app")
