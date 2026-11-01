from alembic import context
from sqlalchemy import create_engine

from northstar_webchat.persistence.tables import metadata

url = context.config.get_main_option("sqlalchemy.url")
assert url is not None
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
