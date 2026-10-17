"""An isolated real PostgreSQL database; never reset the demo database."""
import os
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sonic.config import Settings
from sonic.db import make_database


@pytest.fixture(scope="session", autouse=True)
def database():
    base = Settings()
    url = os.environ.get('SONIC_TEST_DATABASE_URL', base.database_url.get_secret_value().rsplit('/', 1)[0] + '/sonic_test')
    if not url.rsplit('/', 1)[-1].endswith('_test'):
        raise RuntimeError('Tests require a dedicated database ending in _test')
    old = {key: os.environ.get(key) for key in ['SONIC_DATABASE_URL', 'SONIC_AUTH_LIMIT', 'SONIC_STORAGE_ROOT', 'SONIC_ALLOW_REGISTRATION']}
    os.environ.update(SONIC_DATABASE_URL=url, SONIC_AUTH_LIMIT='10000', SONIC_STORAGE_ROOT='.runtime/test-audio', SONIC_ALLOW_REGISTRATION='true')
    command.upgrade(Config('alembic.ini'), 'head')
    engine, _ = make_database(Settings())
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE users, rate_limits CASCADE'))
    engine.dispose()
    yield
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
