"""M1d append-only camera view reference and check records.

Revision ID: 0003_m1d
Revises: 0002_m1c
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_m1d'
down_revision = '0002_m1c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('camera_view_references',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('camera_uuid', sa.String(36), nullable=False),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=False),
        sa.Column('catalog_revision', sa.String(120), nullable=False),
        sa.Column('catalog_sha256', sa.String(64), nullable=False),
        sa.Column('media_sha256', sa.String(64), nullable=False),
        sa.Column('note', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_view_refs_camera', 'camera_view_references', ['camera_uuid', 'created_at'])
    op.create_table('view_comparison_records',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('inspection_id', sa.String(36), sa.ForeignKey('inspection_reports.id'), nullable=False),
        sa.Column('reference_id', sa.String(36), sa.ForeignKey('camera_view_references.id'), nullable=False),
        sa.Column('camera_uuid', sa.String(36), nullable=False),
        sa.Column('catalog_revision', sa.String(120), nullable=False),
        sa.Column('binding_decision_id', sa.String(36), sa.ForeignKey('camera_binding_decisions.id'), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_view_checks_inspection', 'view_comparison_records', ['inspection_id', 'created_at'])


def downgrade():
    op.drop_index('ix_view_checks_inspection', table_name='view_comparison_records')
    op.drop_table('view_comparison_records')
    op.drop_index('ix_view_refs_camera', table_name='camera_view_references')
    op.drop_table('camera_view_references')
