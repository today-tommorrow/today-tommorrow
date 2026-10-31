import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    metadata = MetaData(schema="today_app")


def include_migration_name(name, type_, parent_names):
    if type_ == "schema":
        return name == "today_app"
    if type_ == "table":
        return (
            parent_names.get("schema_name") == "today_app"
            and parent_names.get("schema_qualified_table_name") in Base.metadata.tables
        )
    return True


def include_migration_object(object_, name, type_, reflected, compare_to):
    table = object_ if type_ == "table" else getattr(object_, "table", None)
    return table is None or table.schema == "today_app"


engine = create_engine(
    os.environ.get("DATABASE_URL", "postgresql+psycopg://today_app:localdev_app@127.0.0.1:55432/work24_dev"),
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3},
)
SessionLocal = sessionmaker(bind=engine)


def get_session():
    with SessionLocal() as session:
        yield session
