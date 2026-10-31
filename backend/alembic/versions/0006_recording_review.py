"""录像资源人员审核字段：文件名兜底来源 + 人工确认/驳回。

Revision ID: 0006_recording_review
Revises: 0005_recordings

- name_source: 当前 camera_name 的来源（none/ocr/filename）
- review_kind: 人工审核结论（none/confirmed/rejected）；重扫不得覆盖人工结论
- reviewer_note: 人工审核填写的依据/备注
"""
from alembic import op
import sqlalchemy as sa

revision = '0006_recording_review'
down_revision = '0005_recordings'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('recording_clips', sa.Column('name_source', sa.String(20),
                                               nullable=False, server_default='none'))
    op.add_column('recording_clips', sa.Column('review_kind', sa.String(20),
                                               nullable=False, server_default='none'))
    op.add_column('recording_clips', sa.Column('reviewer_note', sa.String(500),
                                               nullable=True))


def downgrade():
    op.drop_column('recording_clips', 'reviewer_note')
    op.drop_column('recording_clips', 'review_kind')
    op.drop_column('recording_clips', 'name_source')
