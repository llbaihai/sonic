from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_database(settings):
    engine = create_engine(settings.database_url.get_secret_value(), pool_pre_ping=True,
                           pool_size=5, max_overflow=5,
                           connect_args={"connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=5000"})
    return engine, sessionmaker(engine, expire_on_commit=False)
