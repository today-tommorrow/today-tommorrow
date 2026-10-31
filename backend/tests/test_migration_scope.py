import unittest

from sqlalchemy import Integer, MetaData, Table
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, include_migration_name, include_migration_object


class MigrationScopeTests(unittest.TestCase):
    def test_new_app_model_is_bound_to_application_schema(self):
        class ScopeProbe(Base):
            __tablename__ = "scope_probe"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

        table = ScopeProbe.__table__
        if not isinstance(table, Table):
            self.fail("app model did not bind to a SQLAlchemy table")
        self.addCleanup(Base.metadata.remove, table)
        self.assertEqual(table.fullname, "today_app.scope_probe")

    def test_migration_reflection_ignores_education_and_public_schemas(self):
        for schema in (None, "public", "work24_bge"):
            with self.subTest(schema=schema):
                self.assertFalse(include_migration_name(schema, "schema", {}))
        self.assertTrue(include_migration_name("today_app", "schema", {}))

    def test_migration_does_not_drop_unmanaged_application_tables(self):
        self.assertFalse(include_migration_name("unmanaged", "table", {
            "schema_name": "today_app", "schema_qualified_table_name": "today_app.unmanaged",
        }))

    def test_education_model_cannot_become_a_migration_target(self):
        education = Table("protected", MetaData(), schema="work24_bge")
        self.assertFalse(include_migration_object(education, "protected", "table", False, None))


if __name__ == "__main__":
    unittest.main()
