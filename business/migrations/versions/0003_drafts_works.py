"""Versioned edit metadata and works backed by real audio assets."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0003'
down_revision = '0002'


def upgrade():
    op.create_table('drafts', sa.Column('project_id', pg.UUID(), sa.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('tracks', pg.JSONB(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('version > 0', name='ck_draft_version'))
    op.create_table('works', sa.Column('id', pg.UUID(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('project_id', pg.UUID(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('asset_id', pg.UUID(), sa.ForeignKey('assets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(120), nullable=False))
    op.create_index('ix_works_project_id', 'works', ['project_id'])
    op.create_index('ix_works_asset_id', 'works', ['asset_id'])


def downgrade():
    op.drop_table('works')
    op.drop_table('drafts')
