"""target spatial observations (B8-S3)

Revision ID: 0014_target_spatial
Revises: 0013_candidate_reviews
"""
import sqlalchemy as sa
from alembic import op

revision = '0014_target_spatial'
down_revision = '0013_candidate_reviews'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'target_spatial_observations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'), index=True, nullable=False),
        sa.Column('observation_id', sa.String(36), sa.ForeignKey('target_observations.id'), index=True, nullable=False),
        sa.Column('camera_uuid', sa.String(64), nullable=True),
        sa.Column('calibration_id', sa.String(64), nullable=True),
        sa.Column('calibration_revision', sa.String(64), nullable=True),
        sa.Column('spatial_level', sa.String(4), nullable=False, server_default='L1'),
        sa.Column('quality', sa.String(40), nullable=False, server_default='region_only_unverified'),
        sa.Column('identity_status', sa.String(32), nullable=False, server_default='candidate_unverified'),
        sa.Column('ground_point', sa.JSON(), nullable=True),
        sa.Column('uncertainty_radius_m', sa.Float(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_spatial_observations_case', 'target_spatial_observations', ['case_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_target_spatial_observations_case', table_name='target_spatial_observations')
    op.drop_table('target_spatial_observations')
