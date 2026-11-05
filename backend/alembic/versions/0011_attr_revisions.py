"""target attribute revisions

Revision ID: 0011_attr_revisions
Revises: 0010_target_attribute_key
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_attr_revisions'
down_revision = '0010_target_attribute_key'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'target_attribute_revisions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('group_id', sa.String(36), sa.ForeignKey('target_element_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subject_id', sa.String(36), nullable=True),
        sa.Column('attribute_key', sa.String(60), nullable=False),
        sa.Column('name', sa.String(40), nullable=False),
        sa.Column('old_value', sa.String(200), nullable=True),
        sa.Column('old_observation_status', sa.String(20), nullable=True),
        sa.Column('old_review_state', sa.String(20), nullable=True),
        sa.Column('new_value', sa.String(200), nullable=True),
        sa.Column('new_observation_status', sa.String(20), nullable=False),
        sa.Column('new_review_state', sa.String(20), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_target_attribute_revisions_group_id', 'target_attribute_revisions', ['group_id'])


def downgrade() -> None:
    op.drop_table('target_attribute_revisions')
