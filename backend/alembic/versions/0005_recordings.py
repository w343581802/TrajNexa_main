"""录像资源接线：案件选中的录像目录、扫描作业、扫描结果。

Revision ID: 0005_recordings
Revises: 0004_cases

主系统只读录像目录：这里只保存「目录指针、扫描作业、读到的角标时间与名称」，
不保存、不复制、不删除任何录像文件。结束时间不在这里推断。
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_recordings'
down_revision = '0004_cases'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('recording_sources',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), nullable=False),
        sa.Column('path', sa.String(512), nullable=False),
        sa.Column('label', sa.String(255), nullable=False, server_default=''),
        sa.Column('auto_scan', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(20), nullable=False, server_default='idle'),
        sa.Column('last_error', sa.String(300), nullable=True),
        sa.ForeignKeyConstraint(['case_id'], ['cases.id'], name='fk_recording_sources_case'),
        sa.UniqueConstraint('case_id', 'path', name='uq_recording_source_case_path'),
    )
    op.create_index('ix_recording_sources_case', 'recording_sources', ['case_id'])

    op.create_table('recording_scan_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_files', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('processed_files', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('added_files', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_files', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_message', sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(['source_id'], ['recording_sources.id'], name='fk_recording_jobs_source'),
    )
    op.create_index('ix_recording_jobs_source', 'recording_scan_jobs', ['source_id'])
    op.create_index('ix_recording_jobs_status_created', 'recording_scan_jobs', ['status', 'created_at'])

    op.create_table('recording_clips',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_id', sa.String(36), nullable=False),
        sa.Column('relpath', sa.String(512), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('mtime_ns', sa.BigInteger(), nullable=False),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('start_local', sa.String(32), nullable=True),
        sa.Column('start_source', sa.String(20), nullable=False, server_default=''),
        sa.Column('camera_name', sa.String(120), nullable=True),
        sa.Column('camera_uuid', sa.String(36), nullable=True),
        sa.Column('match_status', sa.String(30), nullable=False, server_default='unscanned'),
        sa.Column('ocr_status', sa.String(20), nullable=False, server_default='not_checked'),
        sa.Column('match_suggestions', sa.JSON(), nullable=True),
        sa.Column('scanned_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['recording_sources.id'], name='fk_recording_clips_source'),
        sa.UniqueConstraint('source_id', 'relpath', name='uq_recording_clip_source_path'),
    )
    op.create_index('ix_recording_clips_source', 'recording_clips', ['source_id'])
    op.create_index('ix_recording_clips_camera', 'recording_clips', ['camera_uuid', 'start_local'])


def downgrade():
    op.drop_index('ix_recording_clips_camera', table_name='recording_clips')
    op.drop_index('ix_recording_clips_source', table_name='recording_clips')
    op.drop_table('recording_clips')
    op.drop_index('ix_recording_jobs_status_created', table_name='recording_scan_jobs')
    op.drop_index('ix_recording_jobs_source', table_name='recording_scan_jobs')
    op.drop_table('recording_scan_jobs')
    op.drop_index('ix_recording_sources_case', table_name='recording_sources')
    op.drop_table('recording_sources')
