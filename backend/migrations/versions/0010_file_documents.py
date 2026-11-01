"""Store private file originals and extraction state."""

import sqlalchemy as sa
from alembic import op

revision = "0010_file_documents"
down_revision = "0009_text_documents"
branch_labels = None
depends_on = None


def upgrade():
    for name, type_, default in [
        ("source_type", sa.Text(), "text"),
        ("display_name", sa.Text(), "직접 입력한 텍스트"),
        ("mime_type", sa.Text(), "text/plain"),
        ("status", sa.Text(), "queued"),
    ]:
        op.add_column(
            "documents",
            sa.Column(name, type_, nullable=False, server_default=default),
            schema="today_app",
        )
    op.add_column("documents", sa.Column("error_code", sa.Text()), schema="today_app")
    op.add_column(
        "documents", sa.Column("raw_content", sa.LargeBinary()), schema="today_app"
    )


def downgrade():
    for name in (
        "raw_content",
        "error_code",
        "status",
        "mime_type",
        "display_name",
        "source_type",
    ):
        op.drop_column("documents", name, schema="today_app")
