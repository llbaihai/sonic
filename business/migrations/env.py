from alembic import context
from sonic.config import Settings
from sonic.db import Base, make_database
from sonic import models

settings = Settings()
if context.is_offline_mode():
    context.configure(url=settings.database_url.get_secret_value(), target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = make_database(settings)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
