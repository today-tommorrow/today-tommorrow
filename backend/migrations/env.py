import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.auth.models import Base
from app.db import include_migration_name, include_migration_object

migration_engine = create_engine(
    os.environ.get(
        "MIGRATION_DATABASE_URL",
        "postgresql+psycopg://today_migrator:localdev_migration@127.0.0.1:55432/work24_dev",
    ),
    poolclass=NullPool,
    connect_args={"connect_timeout": 3},
)

# The local bootstrap owns namespace/role creation; migrations own app objects.
with migration_engine.begin() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        version_table_schema="today_app",
        include_schemas=True,
        include_name=include_migration_name,
        include_object=include_migration_object,
    )
    with context.begin_transaction():
        context.run_migrations()
    # Default app-table grants must not allow the runtime to rewrite migration history.
    connection.exec_driver_sql("REVOKE ALL ON TABLE today_app.alembic_version FROM today_app")
    connection.exec_driver_sql("GRANT SELECT ON TABLE today_app.alembic_version TO today_app")
