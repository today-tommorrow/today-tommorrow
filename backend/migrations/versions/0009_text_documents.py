"""Store private text documents and their provenance chunks."""

import sqlalchemy as sa
from alembic import op

revision = "0009_text_documents"
down_revision = "0008_merge_saved_recommendations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documents",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_id", "user_id"],
            ["today_app.intake_profiles.id", "today_app.intake_profiles.user_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "profile_id", "user_id"),
        sa.CheckConstraint(
            "kind IN ('resume','job_description','portfolio','other')",
            name="document_kind",
        ),
        sa.CheckConstraint("size_bytes > 0", name="document_size"),
        schema="today_app",
    )
    op.create_index(
        "ix_today_app_documents_profile_id",
        "documents",
        ["profile_id"],
        schema="today_app",
    )
    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("locator", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id", "profile_id", "user_id"],
            [
                "today_app.documents.id",
                "today_app.documents.profile_id",
                "today_app.documents.user_id",
            ],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="document_chunk_ordinal"),
        schema="today_app",
    )


def downgrade():
    op.drop_table("document_chunks", schema="today_app")
    op.drop_index(
        "ix_today_app_documents_profile_id", table_name="documents", schema="today_app"
    )
    op.drop_table("documents", schema="today_app")
