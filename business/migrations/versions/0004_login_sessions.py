"""Persistent revocable login sessions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
revision='0004'
down_revision='0003'
def upgrade():
    op.create_table('login_sessions',sa.Column('id',pg.UUID(),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('user_id',pg.UUID(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revoked',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_index('ix_login_sessions_user_id','login_sessions',['user_id'])
def downgrade():
    op.drop_table('login_sessions')
