"""Merge the independent saved-course and recommendation migration branches."""

revision = "0008_merge_saved_recommendations"
down_revision = ("0007_saved_courses", "0007_recommendation_runs")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
