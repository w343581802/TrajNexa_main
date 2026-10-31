"""案件表与素材归属案件。

Revision ID: 0004_cases
Revises: 0003_m1d

案件只持有上传的要素文件（media_assets.case_id）；
录像资源不进入主系统数据库，删除案件不会删除录像。
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_cases'
down_revision = '0003_m1d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('cases',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('code', sa.String(40), nullable=False, unique=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column('media_assets', sa.Column('case_id', sa.String(36), nullable=True))
    op.create_foreign_key('fk_media_assets_case', 'media_assets', 'cases', ['case_id'], ['id'])
    op.create_index('ix_media_assets_case', 'media_assets', ['case_id'])


def downgrade():
    op.drop_index('ix_media_assets_case', table_name='media_assets')
    op.drop_constraint('fk_media_assets_case', 'media_assets', type_='foreignkey')
    op.drop_column('media_assets', 'case_id')
    op.drop_table('cases')
