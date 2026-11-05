"""ai prompt settings per object class

Revision ID: 0012_ai_prompt_settings
Revises: 0011_attr_revisions
"""
from alembic import op
import sqlalchemy as sa

revision = '0012_ai_prompt_settings'
down_revision = '0011_attr_revisions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_prompt_settings',
        sa.Column('key', sa.String(20), primary_key=True),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('ai_prompt_settings')
