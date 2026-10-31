"""Auth schema expectations from the agreed database design, on the local DB."""

import os
import unittest

from sqlalchemy import inspect
from test_database_boundary import local_engine


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "run local DB integration after setup")
class AuthSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("MIGRATION_DATABASE_URL", "today_migrator")
        cls.addClassCleanup(cls.engine.dispose)

    def test_authentication_tables_exist_only_in_application_schema(self):
        inspector = inspect(self.engine)
        for table in ("users", "auth_sessions", "oauth_login_states"):
            with self.subTest(table=table):
                self.assertTrue(inspector.has_table(table, schema="today_app"))
                self.assertFalse(inspector.has_table(table, schema="public"))

    def test_user_and_session_fields_match_the_auth_contract(self):
        inspector = inspect(self.engine)
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("users", schema="today_app")},
            {"id", "google_sub", "email", "display_name", "avatar_url", "created_at", "updated_at"},
        )
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("auth_sessions", schema="today_app")},
            {"id", "user_id", "token_hash", "csrf_token_hash", "created_at", "expires_at", "revoked_at"},
        )
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("oauth_login_states", schema="today_app")},
            {"id", "state_hash", "browser_binding_hash", "nonce_hash", "created_at", "expires_at", "consumed_at"},
        )

    def test_google_identity_and_tokens_are_unique_and_sessions_follow_user_deletion(self):
        inspector = inspect(self.engine)
        expected = {"users": "google_sub", "auth_sessions": "token_hash", "oauth_login_states": "state_hash"}
        for table, column in expected.items():
            with self.subTest(table=table):
                unique = inspector.get_unique_constraints(table, schema="today_app")
                self.assertIn([column], [item["column_names"] for item in unique])
        foreign = inspector.get_foreign_keys("auth_sessions", schema="today_app")
        self.assertTrue(any(
            key["constrained_columns"] == ["user_id"] and key["referred_schema"] == "today_app"
            and key["referred_table"] == "users" and key.get("options", {}).get("ondelete") == "CASCADE"
            for key in foreign
        ))
