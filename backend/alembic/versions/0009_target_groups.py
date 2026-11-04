"""B8-B0 composite target element groups (priority objects + relations + attributes).

Revision ID: 0009_target_groups
Revises: 0008_tracking_runs
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_target_groups'
down_revision = '0008_tracking_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'target_element_groups',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('case_id', sa.String(36), sa.ForeignKey('cases.id'), nullable=False),
        sa.Column('media_id', sa.String(36), sa.ForeignKey('media_assets.id'), nullable=False),
        sa.Column('primary_object_id', sa.String(36), nullable=False),
        sa.Column('source_json', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('status', sa.String(16), nullable=False, server_default='saved'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_groups_case_created', 'target_element_groups',
                    ['case_id', 'created_at'])

    op.create_table(
        'target_element_objects',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('group_id', sa.String(36),
                  sa.ForeignKey('target_element_groups.id'), nullable=False),
        sa.Column('object_class', sa.String(16), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('bbox_norm_xyxy', sa.JSON(), nullable=False),
        sa.Column('m4_observation_id', sa.String(36), nullable=True),
        sa.Column('source', sa.String(24), nullable=False, server_default='human_selected'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_objects_group', 'target_element_objects',
                    ['group_id', 'created_at'])

    op.create_table(
        'target_object_relations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('group_id', sa.String(36),
                  sa.ForeignKey('target_element_groups.id'), nullable=False),
        sa.Column('subject_id', sa.String(36), nullable=False),
        sa.Column('predicate', sa.String(24), nullable=False),
        sa.Column('object_id', sa.String(36), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='image_observed'),
        sa.Column('temporal_verified', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('evidence_media_id', sa.String(36), nullable=True),
        sa.Column('note', sa.String(500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_relations_group', 'target_object_relations',
                    ['group_id', 'created_at'])

    op.create_table(
        'target_object_attributes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('group_id', sa.String(36),
                  sa.ForeignKey('target_element_groups.id'), nullable=False),
        sa.Column('subject_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(40), nullable=False),
        sa.Column('value', sa.String(200), nullable=True),
        sa.Column('observation_status', sa.String(16), nullable=False, server_default='unknown'),
        sa.Column('review_state', sa.String(20), nullable=False, server_default='unknown'),
        sa.Column('source', sa.String(40), nullable=False, server_default='human'),
        sa.Column('provenance_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_attrs_group_subject', 'target_object_attributes',
                    ['group_id', 'subject_id'])


def downgrade():
    op.drop_index('ix_target_attrs_group_subject', table_name='target_object_attributes')
    op.drop_table('target_object_attributes')
    op.drop_index('ix_target_relations_group', table_name='target_object_relations')
    op.drop_table('target_object_relations')
    op.drop_index('ix_target_objects_group', table_name='target_element_objects')
    op.drop_table('target_element_objects')
    op.drop_index('ix_target_groups_case_created', table_name='target_element_groups')
    op.drop_table('target_element_groups')
