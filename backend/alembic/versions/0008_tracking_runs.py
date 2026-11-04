"""B8-B case-scoped tracking runs (persisted, resumable).

Revision ID: 0008_tracking_runs
Revises: 0007_m4a_observations
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_tracking_runs'
down_revision = '0007_m4a_observations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tracking_runs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('observation_id', sa.String(36),
                  sa.ForeignKey('target_observations.id'), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='queued'),
        sa.Column('anchor_json', sa.JSON(), nullable=False),
        sa.Column('scope_json', sa.JSON(), nullable=False),
        sa.Column('progress_json', sa.JSON(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_code', sa.String(60), nullable=True),
        sa.Column('error_message', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_tracking_runs_case_id', 'tracking_runs', ['case_id'])
    op.create_index('ix_tracking_runs_case_status', 'tracking_runs',
                    ['case_id', 'status', 'created_at'])

    op.create_table(
        'tracking_windows',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('tracking_runs.id'), nullable=False),
        sa.Column('camera_label', sa.String(120), nullable=False),
        sa.Column('clip_id', sa.String(36), nullable=False),
        sa.Column('window_start', sa.Float(), nullable=False),
        sa.Column('window_end', sa.Float(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='unknown'),
        sa.Column('tracks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_json', sa.JSON(), nullable=False),
        sa.Column('note', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tracking_windows_run_id', 'tracking_windows', ['run_id'])
    op.create_index('ix_tracking_windows_run_created', 'tracking_windows',
                    ['run_id', 'created_at'])

    op.create_table(
        'tracking_candidates',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('tracking_runs.id'), nullable=False),
        sa.Column('candidate_key', sa.String(200), nullable=False),
        sa.Column('camera_label', sa.String(120), nullable=False),
        sa.Column('map_camera_id', sa.String(64), nullable=True),
        sa.Column('track_id', sa.String(120), nullable=False),
        sa.Column('state', sa.String(24), nullable=False, server_default='weak_candidate'),
        sa.Column('identity_status', sa.String(32), nullable=False,
                  server_default='candidate_unverified'),
        sa.Column('retrieval_score', sa.Float(), nullable=True),
        sa.Column('margin_top1_top2', sa.Float(), nullable=True),
        sa.Column('window_start', sa.Float(), nullable=True),
        sa.Column('window_end', sa.Float(), nullable=True),
        sa.Column('evidence_refs', sa.JSON(), nullable=False),
        sa.Column('details_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('run_id', 'candidate_key', name='uq_tracking_candidate_key'),
    )
    op.create_index('ix_tracking_candidates_run_id', 'tracking_candidates', ['run_id'])
    op.create_index('ix_tracking_candidates_run_state', 'tracking_candidates',
                    ['run_id', 'state'])

    op.create_table(
        'tracking_review_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('tracking_runs.id'), nullable=False),
        sa.Column('candidate_id', sa.String(36),
                  sa.ForeignKey('tracking_candidates.id'), nullable=False),
        sa.Column('decision', sa.String(16), nullable=False),
        sa.Column('reviewer', sa.String(120), nullable=False, server_default='human_reviewer'),
        sa.Column('note', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tracking_review_events_run_id', 'tracking_review_events', ['run_id'])
    op.create_index('ix_tracking_reviews_run_created', 'tracking_review_events',
                    ['run_id', 'created_at'])


def downgrade():
    op.drop_index('ix_tracking_reviews_run_created', table_name='tracking_review_events')
    op.drop_index('ix_tracking_review_events_run_id', table_name='tracking_review_events')
    op.drop_table('tracking_review_events')
    op.drop_index('ix_tracking_candidates_run_state', table_name='tracking_candidates')
    op.drop_index('ix_tracking_candidates_run_id', table_name='tracking_candidates')
    op.drop_table('tracking_candidates')
    op.drop_index('ix_tracking_windows_run_created', table_name='tracking_windows')
    op.drop_index('ix_tracking_windows_run_id', table_name='tracking_windows')
    op.drop_table('tracking_windows')
    op.drop_index('ix_tracking_runs_case_status', table_name='tracking_runs')
    op.drop_index('ix_tracking_runs_case_id', table_name='tracking_runs')
    op.drop_table('tracking_runs')
