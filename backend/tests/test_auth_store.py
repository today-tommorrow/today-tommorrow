"""Authentication persistence contracts against the isolated local PostgreSQL DB."""

import hashlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.auth.models import AuthSession, OAuthLoginState, User
from app.auth.store import AuthStore

NOW = datetime(2026, 11, 1, 3, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
KEY = b"synthetic-test-signing-key-32-bytes"


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "run local DB integration after setup")
class AuthStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.db = Session(self.engine)
        self.store = AuthStore(self.db, signing_key=KEY)
        self.addCleanup(self.db.close)
        self.addCleanup(self.db.rollback)

    def user(self, **values):
        return self.store.save_google_identity(
            subject="synthetic-auth-test-" + uuid4().hex,
            email="synthetic@example.invalid", now=NOW, **values,
        )

    def test_google_subject_is_identity_and_relogin_refreshes_loaded_profile(self):
        user = self.user(display_name="Before")
        original_id, created_at, subject = user.id, user.created_at, user.google_sub
        updated = self.store.save_google_identity(
            subject=subject, email="changed@example.invalid", display_name="After",
            avatar_url="https://example.invalid/avatar", now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(updated.id, original_id)
        self.assertEqual(updated.created_at, created_at)
        self.assertEqual(updated.display_name, "After")
        self.assertEqual(updated.email, "changed@example.invalid")
        self.assertEqual(updated.updated_at, NOW + timedelta(minutes=1))
        self.assertNotEqual(self.user().id, original_id)

    def test_equal_email_does_not_merge_distinct_google_subjects(self):
        first, second = self.user(), self.user()
        self.assertEqual(first.email, second.email)
        self.assertNotEqual(first.id, second.id)

    def test_session_stores_only_hashes_and_reconstructs_csrf_after_reload(self):
        user = self.user()
        issued = self.store.issue_session(user, now=NOW, expires_at=LATER)
        row = self.db.get(AuthSession, issued.id)
        assert row is not None
        self.assertEqual(row.token_hash, hashlib.sha256(issued.token.encode()).hexdigest())
        self.assertEqual(row.csrf_token_hash, hashlib.sha256(issued.csrf_token.encode()).hexdigest())
        self.assertNotEqual(issued.token, issued.csrf_token)
        self.assertNotIn(issued.token, repr(issued))
        self.assertNotIn(issued.csrf_token, repr(issued))
        self.db.expunge_all()
        principal = self.store.get_session(issued.token, now=NOW)
        assert principal is not None
        self.assertEqual(principal.user.id, issued.user_id)
        self.assertEqual(principal.session_id, issued.id)
        self.assertEqual(principal.expires_at, LATER)
        self.assertEqual(principal.csrf_token, issued.csrf_token)
        self.assertNotIn(principal.csrf_token, repr(principal))
        self.assertTrue(self.store.verify_csrf(issued.token, issued.csrf_token, now=NOW))
        self.assertFalse(self.store.verify_csrf(issued.token, "incorrect", now=NOW))
        other_key = AuthStore(self.db, signing_key=b"a-different-synthetic-key-32-bytes")
        self.assertIsNone(other_key.get_session(issued.token, now=NOW))
        self.assertFalse(other_key.verify_csrf(issued.token, issued.csrf_token, now=NOW))

    def test_missing_future_and_expired_sessions_are_rejected(self):
        issued = self.store.issue_session(self.user(), now=NOW, expires_at=LATER)
        for token, now in ((None, NOW), ("", NOW), ("unknown", NOW),
                           (issued.token, NOW - timedelta(microseconds=1)), (issued.token, LATER)):
            with self.subTest(token_kind="missing" if not token else "provided", now=now):
                self.assertIsNone(self.store.get_session(token, now=now))
                self.assertFalse(self.store.verify_csrf(token, issued.csrf_token, now=now))

    def test_revocation_is_idempotent_and_prevents_reuse(self):
        issued = self.store.issue_session(self.user(), now=NOW, expires_at=LATER)
        self.assertTrue(self.store.revoke_session(issued.token, now=NOW))
        self.assertFalse(self.store.revoke_session(issued.token, now=NOW))
        self.assertFalse(self.store.revoke_session(None, now=NOW))
        self.assertFalse(self.store.revoke_session("unknown", now=NOW))
        self.assertIsNone(self.store.get_session(issued.token, now=NOW))
        self.assertFalse(self.store.verify_csrf(issued.token, issued.csrf_token, now=NOW))

    def test_oauth_secrets_are_hashed_and_state_is_browser_bound_and_single_use(self):
        issued = self.store.issue_oauth_state(now=NOW, expires_at=LATER)
        row = self.db.get(OAuthLoginState, issued.id)
        assert row is not None
        for field, secret in (("state_hash", issued.state),
                              ("browser_binding_hash", issued.browser_binding), ("nonce_hash", issued.nonce)):
            self.assertEqual(getattr(row, field), hashlib.sha256(secret.encode()).hexdigest())
            self.assertNotIn(secret, repr(issued))
        self.assertEqual(len({issued.state, issued.browser_binding, issued.nonce}), 3)
        self.assertIsNone(self.store.consume_oauth_state(issued.state, "wrong-browser", now=NOW))
        self.assertIsNone(self.store.consume_oauth_state("unknown", issued.browser_binding, now=NOW))
        self.assertIsNone(self.store.consume_oauth_state(None, None, now=NOW))
        consumed = self.store.consume_oauth_state(issued.state, issued.browser_binding, now=NOW)
        assert consumed is not None
        self.assertEqual(consumed.id, issued.id)
        self.assertEqual(consumed.consumed_at, NOW)
        self.assertTrue(self.store.nonce_matches(consumed, issued.nonce))
        self.assertFalse(self.store.nonce_matches(consumed, "wrong-nonce"))
        self.assertFalse(self.store.nonce_matches(consumed, None))
        self.assertIsNone(self.store.consume_oauth_state(issued.state, issued.browser_binding, now=NOW))

    def test_future_and_expired_oauth_states_are_rejected_without_consumption(self):
        issued = self.store.issue_oauth_state(now=NOW, expires_at=LATER)
        for now in (NOW - timedelta(microseconds=1), LATER):
            self.assertIsNone(self.store.consume_oauth_state(issued.state, issued.browser_binding, now=now))
        self.assertIsNotNone(self.store.consume_oauth_state(issued.state, issued.browser_binding, now=NOW))

    def test_two_connections_consume_oauth_state_with_one_winner(self):
        issued = self.store.issue_oauth_state(now=NOW, expires_at=LATER)
        self.db.commit()
        self.addCleanup(self.delete_committed_state, issued.id)
        barrier = Barrier(2)

        def consume():
            with Session(self.engine) as db:
                db.connection()
                barrier.wait(timeout=5)
                result = AuthStore(db, signing_key=KEY).consume_oauth_state(
                    issued.state, issued.browser_binding, now=NOW,
                )
                winner = result is not None
                db.commit()
                return winner

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: consume(), range(2)))
        self.assertEqual(sorted(results), [False, True])

    def delete_committed_state(self, state_id):
        with Session(self.engine) as db:
            db.execute(delete(OAuthLoginState).where(OAuthLoginState.id == state_id))
            db.commit()

    def test_caller_can_rollback_all_issued_records(self):
        user = self.user()
        issued_session = self.store.issue_session(user, now=NOW, expires_at=LATER)
        issued_state = self.store.issue_oauth_state(now=NOW, expires_at=LATER)
        user_id = user.id
        self.db.rollback()
        self.assertIsNone(self.db.get(User, user_id))
        self.assertIsNone(self.db.get(AuthSession, issued_session.id))
        self.assertIsNone(self.db.get(OAuthLoginState, issued_state.id))

    def test_caller_can_rollback_state_consumption(self):
        issued = self.store.issue_oauth_state(now=NOW, expires_at=LATER)
        self.db.commit()
        self.addCleanup(self.delete_committed_state, issued.id)
        self.assertIsNotNone(self.store.consume_oauth_state(issued.state, issued.browser_binding, now=NOW))
        self.db.rollback()
        self.assertIsNotNone(self.store.consume_oauth_state(issued.state, issued.browser_binding, now=NOW))
        self.db.rollback()

    def test_creation_rejects_naive_or_unordered_times_and_empty_identity(self):
        user = self.user()
        naive = NOW.replace(tzinfo=None)
        for now, expires_at in ((naive, LATER), (NOW, naive), (NOW, NOW), (LATER, NOW)):
            with self.subTest(now=now, expires_at=expires_at):
                with self.assertRaises(ValueError):
                    self.store.issue_session(user, now=now, expires_at=expires_at)
                with self.assertRaises(ValueError):
                    self.store.issue_oauth_state(now=now, expires_at=expires_at)
        for subject, email in (("", "synthetic@example.invalid"), ("synthetic", "")):
            with self.assertRaises(ValueError):
                self.store.save_google_identity(subject=subject, email=email, now=NOW)
        with self.assertRaises(ValueError):
            self.store.save_google_identity(subject="synthetic", email="synthetic@example.invalid", now=naive)

    def test_lookup_rejects_naive_times(self):
        naive = NOW.replace(tzinfo=None)
        for call in (
            lambda: self.store.get_session(None, now=naive),
            lambda: self.store.verify_csrf(None, None, now=naive),
            lambda: self.store.revoke_session(None, now=naive),
            lambda: self.store.consume_oauth_state(None, None, now=naive),
        ):
            with self.assertRaises(ValueError):
                call()


class AuthStoreConfigurationTests(unittest.TestCase):
    def test_signing_key_is_explicit_and_at_least_256_bits(self):
        with Session() as db:
            invalid_keys: tuple[Any, ...] = (b"", b"a" * 31, "a" * 32, None)
            for key in invalid_keys:
                with self.subTest(key_kind=type(key).__name__), self.assertRaises(ValueError):
                    AuthStore(db, signing_key=key)
