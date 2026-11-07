"""target element anchors (B8-P1)

Revision ID: 0015_element_anchors
Revises: 0014_target_spatial

一个「目标要素对象」的定位结论：截图时刻 + 镜头 + 可能位置 + 时间框标签偏移 + 轨迹回填状态。
定位点与时间框是两件事：label_offset 只移动展示标签，ground_point 不被拖动改写。
"""
import sqlalchemy as sa
from alembic import op

revision = '0015_element_anchors'
down_revision = '0014_target_spatial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'target_element_anchors',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'), index=True, nullable=False),
        sa.Column('group_id', sa.String(36),
                  sa.ForeignKey('target_element_groups.id', ondelete='CASCADE'), index=True,
                  nullable=False),
        sa.Column('object_id', sa.String(36), nullable=False),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=True),
        sa.Column('camera_uuid', sa.String(64), nullable=True),
        sa.Column('camera_label', sa.String(120), nullable=False, server_default=''),
        sa.Column('camera_source', sa.String(24), nullable=False, server_default='none'),
        sa.Column('capture_local', sa.String(19), nullable=True),
        sa.Column('capture_source', sa.String(24), nullable=False, server_default='none'),
        sa.Column('capture_verified', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('pixel_xy', sa.JSON(), nullable=True),
        sa.Column('frame_size', sa.JSON(), nullable=True),
        sa.Column('projection_level', sa.String(4), nullable=False, server_default='L0'),
        sa.Column('projection_quality', sa.String(40), nullable=False,
                  server_default='region_only_unverified'),
        sa.Column('ground_point', sa.JSON(), nullable=True),
        sa.Column('uncertainty_radius_m', sa.Float(), nullable=True),
        sa.Column('projection_payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('label_offset', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('trace_run_id', sa.String(36), nullable=True),
        sa.Column('trace_status', sa.String(16), nullable=False, server_default='none'),
        sa.Column('trace_window', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_element_anchors_case', 'target_element_anchors',
                    ['case_id', 'created_at'])
    op.create_index('ix_target_element_anchors_group', 'target_element_anchors',
                    ['group_id', 'object_id'])


def downgrade() -> None:
    op.drop_index('ix_target_element_anchors_group', table_name='target_element_anchors')
    op.drop_index('ix_target_element_anchors_case', table_name='target_element_anchors')
    op.drop_table('target_element_anchors')