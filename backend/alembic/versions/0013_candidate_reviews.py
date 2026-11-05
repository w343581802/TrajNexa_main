"""tracking candidate visual reviews

Revision ID: 0013_candidate_reviews
Revises: 0012_ai_prompt_settings
"""
from alembic import op
import sqlalchemy as sa

revision = '0013_candidate_reviews'
down_revision = '0012_ai_prompt_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tracking_candidate_reviews',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('tracking_runs.id'), nullable=False),
        sa.Column('candidate_id', sa.String(36), sa.ForeignKey('tracking_candidates.id'), nullable=False),
        sa.Column('camera_label', sa.String(120), nullable=False),
        sa.Column('overall', sa.String(32), nullable=False),
        sa.Column('model', sa.String(160), nullable=True),
        sa.Column('prompt_sha256', sa.String(64), nullable=True),
        sa.Column('error_code', sa.String(80), nullable=True),
        sa.Column('review_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tracking_candidate_reviews_run', 'tracking_candidate_reviews', ['run_id', 'created_at'])
    op.create_index('ix_tracking_candidate_reviews_candidate_id', 'tracking_candidate_reviews', ['candidate_id'])


def downgrade() -> None:
    op.drop_table('tracking_candidate_reviews')
