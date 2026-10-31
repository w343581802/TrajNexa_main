"""M4a human-selected image observations (append-only evidence).
Revision ID: 0007_m4a_observations
Revises: 0006_recording_review
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_m4a_observations'
down_revision = '0006_recording_review'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'target_observations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=False),
        sa.Column('object_class', sa.String(24), nullable=False),
        sa.Column('analysis_json', sa.JSON(), nullable=False),
        sa.Column('review_note', sa.String(500), nullable=False),
        sa.Column('review_state', sa.String(32), nullable=False, server_default='pending'),
        sa.Column('source_inspection_id', sa.String(36),
                  sa.ForeignKey('inspection_reports.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_observations_case_created', 'target_observations',
                    ['case_id', 'created_at'])
    op.create_index('ix_target_observations_media_created', 'target_observations',
                    ['media_id', 'created_at'])


def downgrade():
    op.drop_index('ix_target_observations_media_created', table_name='target_observations')
    op.drop_index('ix_target_observations_case_created', table_name='target_observations')
    op.drop_table('target_observations')
