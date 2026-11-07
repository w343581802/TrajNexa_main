"""track samples (B8-SA5)

Revision ID: 0016_track_samples
Revises: 0015_element_anchors

逐帧轨迹样本：把 Tracklet 的每帧像素框落库，供「真实案件实验空间轨迹」读取时现算。
只存视频侧事实（帧号、窗口内秒数、框、画幅），不存投影坐标，也不存身份结论。
"""
import sqlalchemy as sa
from alembic import op

revision = '0016_track_samples'
down_revision = '0015_element_anchors'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'track_samples',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('tracking_runs.id'),
                  index=True, nullable=False),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'),
                  index=True, nullable=False),
        sa.Column('candidate_id', sa.String(36),
                  sa.ForeignKey('tracking_candidates.id', ondelete='CASCADE'),
                  index=True, nullable=True),
        sa.Column('window_id', sa.String(36), nullable=True),
        sa.Column('camera_label', sa.String(120), nullable=False, server_default=''),
        sa.Column('track_id', sa.String(120), nullable=False, server_default=''),
        sa.Column('frame_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('timestamp_seconds', sa.Float(), nullable=False, server_default='0'),
        sa.Column('bbox_json', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('frame_width', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('frame_height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('person_confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
    )
    op.create_index('ix_track_samples_run_track', 'track_samples',
                    ['run_id', 'track_id', 'timestamp_seconds'])
    op.create_index('ix_track_samples_candidate', 'track_samples', ['candidate_id'])


def downgrade() -> None:
    op.drop_index('ix_track_samples_candidate', table_name='track_samples')
    op.drop_index('ix_track_samples_run_track', table_name='track_samples')
    op.drop_table('track_samples')