"""Real local PostgreSQL sessions; only Google's external exchange is replaced."""

import hashlib
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from test_database_boundary import local_engine

from app.auth.models import AuthSession, OAuthLoginState, User
from app.db import get_session
from app.main import app

ORIGIN = "http://127.0.0.1:5173"
ENV = {
    "PUBLIC_ORIGIN": ORIGIN,
    "GOOGLE_REDIRECT_URI": ORIGIN + "/api/auth/google/callback",
    "GOOGLE_CLIENT_ID": "synthetic-client.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "synthetic-client-secret",
    "SESSION_SECRET": "synthetic-signing-key-at-least-32-bytes",
    "SESSION_TTL_SECONDS": "28800", "OAUTH_STATE_TTL_SECONDS": "600",
}


class AnonymousAuthTests(unittest.TestCase):
    def test_me_without_cookie_is_401_even_without_google_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            response = TestClient(app).get("/api/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthenticated")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_missing_configuration_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            response = TestClient(app).get("/api/auth/google/start")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "auth_unavailable")


@unittest.skipUnless(os.environ.get("L0_DATABASE_TESTS") == "1", "run after local DB setup")
class AuthHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = local_engine("DATABASE_URL", "today_app")
        cls.addClassCleanup(cls.engine.dispose)

    def setUp(self):
        self.subject = "synthetic-http-" + uuid4().hex
        self.state_hashes = []
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.clean_rows)
        def session():
            with Session(self.engine) as db:
                yield db
        app.dependency_overrides[get_session] = session
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        self.addCleanup(self.client.close)

    def clean_rows(self):
        with Session(self.engine) as db, db.begin():
            db.execute(delete(User).where(User.google_sub == self.subject))
            db.execute(delete(OAuthLoginState).where(OAuthLoginState.state_hash.in_(self.state_hashes)))

    def start(self):
        response = self.client.get("/api/auth/google/start")
        self.assertEqual(response.status_code, 302)
        url = urlsplit(response.headers["location"])
        self.assertEqual(url.scheme + "://" + url.netloc + url.path, "https://accounts.google.com/o/oauth2/v2/auth")
        query = parse_qs(url.query)
        self.assertEqual(query["redirect_uri"], [ENV["GOOGLE_REDIRECT_URI"]])
        self.assertEqual(query["scope"], ["openid email profile"])
        self.assertEqual(query["response_type"], ["code"])
        state, nonce = query["state"][0], query["nonce"][0]
        digest = hashlib.sha256(state.encode()).hexdigest()
        self.state_hashes.append(digest)
        with Session(self.engine) as db:
            row = db.scalar(select(OAuthLoginState).where(OAuthLoginState.state_hash == digest))
            self.assertIsNotNone(row)
            assert row is not None
            self.assertIsNone(row.consumed_at)
            self.assertEqual(row.nonce_hash, hashlib.sha256(nonce.encode()).hexdigest())
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertNotIn("Secure", cookie)
        return state, nonce

    def callback(self, state, **kwargs):
        return self.client.get("/api/auth/google/callback", params={"state": state, **kwargs})

    def login(self):
        from app.auth.google import GoogleIdentity
        state, nonce = self.start()
        def exchange(**kwargs):
            # State consumption must already be committed on another connection.
            with Session(self.engine) as db:
                row = db.scalar(select(OAuthLoginState).where(OAuthLoginState.state_hash == self.state_hashes[-1]))
                assert row is not None
                self.assertIsNotNone(row.consumed_at)
            self.assertEqual(kwargs["redirect_uri"], ENV["GOOGLE_REDIRECT_URI"])
            return GoogleIdentity(self.subject, "synthetic@example.invalid", "합성 사용자", None, nonce)
        with patch("app.auth.routes.exchange_identity", side_effect=exchange):
            response = self.callback(state, code="synthetic-code")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], ORIGIN + "/onboarding")
        cookie = response.headers.get_list("set-cookie")[0]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        return state

    def test_login_me_csrf_logout_and_session_revocation(self):
        self.login()
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"user", "csrf_token", "expires_at"})
        self.assertEqual(body["user"]["email"], "synthetic@example.invalid")
        self.assertNotIn("google_sub", body["user"])
        self.assertGreater(datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")), datetime.now(UTC))
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code, 403)
        headers = {"X-CSRF-Token": body["csrf_token"], "Origin": "https://example.invalid"}
        self.assertEqual(self.client.post("/api/auth/logout", headers=headers).status_code, 403)
        headers["Origin"] = ORIGIN
        token = self.client.cookies.get("today_tommorrow_session")
        response = self.client.post("/api/auth/logout", headers=headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertIsNone(self.client.cookies.get("today_tommorrow_session"))
        self.client.cookies.set("today_tommorrow_session", token)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_callback_replay_and_browser_binding_rejected_before_google(self):
        state, _ = self.start()
        with patch("app.auth.routes.exchange_identity") as exchange:
            other = TestClient(app, base_url=ORIGIN, follow_redirects=False)
            self.assertEqual(other.get("/api/auth/google/callback", params={"state": state, "code": "synthetic"}).status_code, 400)
            exchange.assert_not_called()
            response = self.callback(state, error="access_denied")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], ORIGIN + "/login?error=oauth_cancelled")
            self.assertEqual(self.callback(state, code="synthetic").status_code, 400)
            exchange.assert_not_called()

    def test_nonce_mismatch_and_provider_failure_leave_state_consumed(self):
        from app.auth.google import GoogleIdentity, GoogleLoginError
        for result in (GoogleIdentity(self.subject, "synthetic@example.invalid", None, None, "wrong"), GoogleLoginError()):
            state, _ = self.start()
            with patch("app.auth.routes.exchange_identity", side_effect=result if isinstance(result, Exception) else None, return_value=result):
                self.assertEqual(self.callback(state, code="synthetic").status_code, 400)
                self.assertEqual(self.callback(state, code="synthetic").status_code, 400)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(User).where(User.google_sub == self.subject)))

    def test_expired_state_and_session_rejected(self):
        state, _ = self.start()
        with Session(self.engine) as db, db.begin():
            db.execute(update(OAuthLoginState).where(OAuthLoginState.state_hash == self.state_hashes[-1]).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        self.assertEqual(self.callback(state, code="synthetic").status_code, 400)
        self.login()
        with Session(self.engine) as db, db.begin():
            db.execute(update(AuthSession).where(AuthSession.user_id.in_(select(User.id).where(User.google_sub == self.subject))).values(expires_at=datetime.now(UTC)-timedelta(seconds=1)))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_relogin_rotates_session_and_preserves_user(self):
        self.login()
        first = self.client.get("/api/auth/me").json()
        old = self.client.cookies.get("today_tommorrow_session")
        self.login()
        second = self.client.get("/api/auth/me").json()
        self.assertEqual(first["user"]["id"], second["user"]["id"])
        self.assertNotEqual(first["csrf_token"], second["csrf_token"])
        self.client.cookies.set("today_tommorrow_session", old)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_duplicate_and_oversize_callback_parameters_rejected(self):
        state, _ = self.start()
        with patch("app.auth.routes.exchange_identity") as exchange:
            response = self.client.get("/api/auth/google/callback", params=[("state", state), ("state", "other"), ("code", "synthetic")])
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.callback(state, code="s"*4097).status_code, 400)
            exchange.assert_not_called()

    def test_http_callback_uses_real_signature_verifier_and_database(self):
        import test_google
        fixture = test_google.GoogleProviderTests()
        fixture.setUpClass()
        state, nonce = self.start()
        signed = fixture.token(fixture.claims(sub=self.subject, nonce=nonce))
        with fixture.network(signed):
            response = self.callback(state, code="synthetic-code")
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["email"], "synthetic@example.invalid")

    def test_callback_removes_credentials_from_access_log_scope(self):
        observed = []
        async def observe(scope, receive, send):
            async def capture(message):
                if message["type"] == "http.response.start":
                    observed.append(scope["query_string"])
                await send(message)
            await app(scope, receive, capture)
        client = TestClient(observe, base_url=ORIGIN, follow_redirects=False)
        response = client.get("/api/auth/google/callback", params={"code": "synthetic-private-code", "state": "synthetic-state"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(observed, [b""])
