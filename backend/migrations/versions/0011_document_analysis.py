"""Add document analysis jobs and durable document review consent."""

import sqlalchemy as sa
from alembic import op

revision = "0011_document_analysis"
down_revision = "0010_file_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE today_app.documents SET status = 'failed', error_code = 'analysis_interrupted' WHERE status = 'queued'"
    )
    op.add_column("documents", sa.Column("job_id", sa.Uuid()), schema="today_app")
    op.add_column(
        "documents",
        sa.Column(
            "review_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema="today_app",
    )
    op.drop_constraint("job_kind", "jobs", schema="today_app", type_="check")
    op.drop_constraint("job_document", "jobs", schema="today_app", type_="check")
    op.create_check_constraint(
        "job_kind",
        "jobs",
        "kind IN ('intake_question','recommendation','document_analysis')",
        schema="today_app",
    )
    op.create_check_constraint(
        "job_document",
        "jobs",
        "(kind = 'document_analysis' AND document_id IS NOT NULL) OR (kind <> 'document_analysis' AND document_id IS NULL)",
        schema="today_app",
    )


def downgrade():
    op.execute("DELETE FROM today_app.jobs WHERE kind = 'document_analysis'")
    op.drop_constraint("job_kind", "jobs", schema="today_app", type_="check")
    op.drop_constraint("job_document", "jobs", schema="today_app", type_="check")
    op.create_check_constraint(
        "job_kind",
        "jobs",
        "kind IN ('intake_question','recommendation')",
        schema="today_app",
    )
    op.create_check_constraint(
        "job_document", "jobs", "document_id IS NULL", schema="today_app"
    )
    op.drop_column("documents", "review_confirmed", schema="today_app")
    op.drop_column("documents", "job_id", schema="today_app")
