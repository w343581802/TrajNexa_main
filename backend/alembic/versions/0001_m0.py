"""M0 core tables

Revision ID: 0001_m0
Revises:
"""
from alembic import op
import sqlalchemy as sa
revision = '0001_m0'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('media_assets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('original_name', sa.String(255), nullable=False),
        sa.Column('mime_detected', sa.String(100), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('storage_relpath', sa.String(255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table('analysis_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=False),
        sa.Column('module', sa.String(60), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(60), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
    )
    op.create_index('ix_jobs_status_created', 'analysis_jobs', ['status', 'created_at'])
    op.create_table('inspection_reports',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('analysis_jobs.id'), nullable=False, unique=True),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=False),
        sa.Column('schema_version', sa.String(20), nullable=False),
        sa.Column('module_version', sa.String(50), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

def downgrade():
    op.drop_table('inspection_reports')
    op.drop_index('ix_jobs_status_created', table_name='analysis_jobs')
    op.drop_table('analysis_jobs')
    op.drop_table('media_assets')
