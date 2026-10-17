"""Validated audio and durable file deletion queue."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0002'
down_revision = '0001'


def upgrade():
    op.create_table('assets', sa.Column('id', pg.UUID(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('project_id', pg.UUID(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('storage_key', sa.String(64), unique=True, nullable=False),
        sa.Column('filename', sa.String(160), nullable=False), sa.Column('media_type', sa.String(64), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False), sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('codec', sa.String(32), nullable=False), sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('sample_rate', sa.Integer(), nullable=False), sa.Column('channels', sa.Integer(), nullable=False),
        sa.CheckConstraint('size_bytes > 0 AND duration_ms > 0', name='ck_asset_positive'))
    op.create_index('ix_assets_project_id', 'assets', ['project_id'])
    op.create_table('file_deletions', sa.Column('storage_key', sa.String(64), primary_key=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table('file_deletions')
    op.drop_table('assets')
