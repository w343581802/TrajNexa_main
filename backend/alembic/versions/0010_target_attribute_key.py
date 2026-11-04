"""target attribute stable key

Revision ID: 0010_target_attribute_key
Revises: 0009_target_groups
"""
from alembic import op
import sqlalchemy as sa

revision = '0010_target_attribute_key'
down_revision = '0009_target_groups'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('target_object_attributes', sa.Column('attribute_key', sa.String(length=60), nullable=True))
    op.execute("UPDATE target_object_attributes SET attribute_key = name WHERE attribute_key IS NULL")

def downgrade() -> None:
    op.drop_column('target_object_attributes', 'attribute_key')
