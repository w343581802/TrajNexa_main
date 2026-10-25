"""M1c append-only camera decisions.

Revision ID: 0002_m1c
Revises: 0001_m0
"""
from alembic import op
import sqlalchemy as sa
revision = '0002_m1c'
down_revision = '0001_m0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('camera_binding_decisions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('inspection_id', sa.String(36), sa.ForeignKey('inspection_reports.id'), nullable=False),
        sa.Column('decision', sa.String(12), nullable=False),
        sa.Column('camera_uuid', sa.String(36), nullable=True),
        sa.Column('camera_name_snapshot', sa.String(120), nullable=True),
        sa.Column('catalog_revision', sa.String(120), nullable=True),
        sa.Column('catalog_sha256', sa.String(64), nullable=True),
        sa.Column('matched_from_ocr', sa.Boolean(), nullable=False),
        sa.Column('note', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_camera_binding_inspection_created', 'camera_binding_decisions',
                    ['inspection_id', 'created_at'])


def downgrade():
    op.drop_index('ix_camera_binding_inspection_created', table_name='camera_binding_decisions')
    op.drop_table('camera_binding_decisions')
