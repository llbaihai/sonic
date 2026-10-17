"""Identity, projects and transactional retry records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0001'
down_revision = None


def upgrade():
    op.create_table('users', sa.Column('id', pg.UUID(), primary_key=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.Column('username', sa.String(64), nullable=False, unique=True),
                    sa.Column('password_hash', sa.String(255), nullable=False))
    op.create_table('projects', sa.Column('id', pg.UUID(), primary_key=True),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                    sa.Column('owner_id', pg.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
                    sa.Column('name', sa.String(120), nullable=False))
    op.create_index('ix_projects_owner_id_id', 'projects', ['owner_id', 'id'])
    op.create_table('idempotency', sa.Column('owner_id', pg.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
                    sa.Column('scope', sa.String(160), primary_key=True), sa.Column('key', sa.String(128), primary_key=True),
                    sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('response', pg.JSONB(), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_table('rate_limits', sa.Column('key', sa.String(64), primary_key=True),
                    sa.Column('window', sa.Integer(), primary_key=True), sa.Column('count', sa.Integer(), nullable=False))


def downgrade():
    for table in ['rate_limits', 'idempotency', 'projects', 'users']:
        op.drop_table(table)
