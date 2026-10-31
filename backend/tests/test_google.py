"""Synthetic signed tokens exercise the real Google verifier without network I/O."""

import base64
import io
import json
import time
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt

from app.auth.google import GoogleIdentity, GoogleLoginError, exchange_identity

CLIENT = "synthetic-client.apps.googleusercontent.com"
REDIRECT = "http://127.0.0.1:5173/api/auth/google/callback"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"


class GoogleProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.signer = crypt.RSASigner.from_string(pem, key_id="synthetic-key")
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic.invalid")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256())
        )
        cls.certs = {"synthetic-key": certificate.public_bytes(serialization.Encoding.PEM).decode()}

    def claims(self, **updates):
        now = int(time.time())
        return {
            "iss": "https://accounts.google.com", "aud": CLIENT,
            "iat": now - 10, "exp": now + 600,
            "sub": "synthetic-subject", "email": "synthetic@example.invalid",
            "email_verified": True, "nonce": "synthetic-nonce",
            "name": "Synthetic User", "picture": "https://example.invalid/avatar.png",
            **updates,
        }

    def token(self, claims=None):
        return jwt.encode(self.signer, self.claims() if claims is None else claims).decode()

    @contextmanager
    def network(self, token=None, *, token_body=None, token_status=200, certs_status=200):
        calls = []
        body = {"id_token": token or self.token()} if token_body is None else token_body

        def request(session, method, url, **kwargs):
            calls.append((method, url, kwargs))
            response = requests.Response()
            response.url = url
            if url == TOKEN_URL:
                response.status_code = token_status
                content = body
            elif url == CERTS_URL:
                response.status_code = certs_status
                content = self.certs
            else:
                raise AssertionError(f"unexpected external URL: {url}")
            response._content = json.dumps(content).encode()
            response.raw = io.BytesIO(response._content)
            return response

        with patch.object(requests.Session, "request", new=request):
            yield calls

    def exchange(self):
        return exchange_identity(
            code="synthetic-code", client_id=CLIENT, client_secret="synthetic-secret",
            redirect_uri=REDIRECT,
        )

    def test_valid_signed_token_returns_identity_and_bounded_google_requests(self):
        with self.network() as calls:
            identity = self.exchange()
        self.assertEqual(identity, GoogleIdentity(
            subject="synthetic-subject", email="synthetic@example.invalid",
            display_name="Synthetic User", avatar_url="https://example.invalid/avatar.png",
            nonce="synthetic-nonce",
        ))
        self.assertNotIn("synthetic-nonce", repr(identity))
        self.assertEqual([(method, url) for method, url, _ in calls], [
            ("POST", TOKEN_URL), ("GET", CERTS_URL),
        ])
        self.assertEqual(calls[0][2]["data"], {
            "grant_type": "authorization_code", "code": "synthetic-code",
            "client_id": CLIENT, "client_secret": "synthetic-secret", "redirect_uri": REDIRECT,
        })
        for _, _, kwargs in calls:
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 10)
            self.assertFalse(kwargs["allow_redirects"])

    def test_alternate_official_google_issuer_is_accepted(self):
        with self.network(self.token(self.claims(iss="accounts.google.com"))):
            self.assertEqual(self.exchange().subject, "synthetic-subject")

    def test_tampered_payload_is_rejected_by_real_signature_verification(self):
        header, _, signature = self.token().split(".")
        payload = base64.urlsafe_b64encode(json.dumps(self.claims(sub="attacker")).encode())
        token = ".".join([header, payload.decode().rstrip("="), signature])
        with self.network(token), self.assertRaises(GoogleLoginError):
            self.exchange()

    def test_malformed_and_unsigned_tokens_are_rejected(self):
        _, payload, _ = self.token().split(".")
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        for token in ("not-a-jwt", "a.b.c", f"{header}.{payload}."):
            with self.subTest(token=token), self.network(token):
                with self.assertRaises(GoogleLoginError):
                    self.exchange()

    def test_empty_oauth_parameters_fail_before_any_external_request(self):
        arguments = {
            "code": "synthetic-code", "client_id": CLIENT,
            "client_secret": "synthetic-secret", "redirect_uri": REDIRECT,
        }
        for field in arguments:
            with self.subTest(field=field), self.network() as calls:
                with self.assertRaises(GoogleLoginError):
                    exchange_identity(**{**arguments, field: ""})
                self.assertEqual(calls, [])

    def test_wrong_audience_issuer_expiry_and_future_issuance_are_rejected(self):
        now = int(time.time())
        for change in (
            {"aud": "other-client"}, {"iss": "https://attacker.invalid"},
            {"exp": now - 60}, {"iat": now + 600},
        ):
            with self.subTest(change=change), self.network(self.token(self.claims(**change))):
                with self.assertRaises(GoogleLoginError):
                    self.exchange()

    def test_missing_required_verifier_claims_are_sanitized(self):
        for field in ("iss", "aud", "iat", "exp"):
            claims = self.claims()
            del claims[field]
            with self.subTest(field=field), self.network(self.token(claims)):
                with self.assertRaises(GoogleLoginError):
                    self.exchange()

    def test_invalid_identity_claims_are_rejected_after_signature_verification(self):
        for change in (
            {"sub": ""}, {"sub": 42}, {"email": "  "}, {"email": None},
            {"email_verified": False}, {"email_verified": "true"},
            {"nonce": ""}, {"nonce": 42},
        ):
            with self.subTest(change=change), self.network(self.token(self.claims(**change))):
                with self.assertRaises(GoogleLoginError):
                    self.exchange()

    def test_optional_profile_values_are_absent_or_safe_strings(self):
        for changes in (
            {"name": None, "picture": None},
            {"name": {"unexpected": "object"}, "picture": 42},
            {"name": "bad\nname", "picture": "javascript:alert(1)"},
            {"name": "x" * 513, "picture": "https://user:secret@example.invalid/a"},
        ):
            with self.subTest(changes=changes), self.network(self.token(self.claims(**changes))):
                identity = self.exchange()
                self.assertIsNone(identity.display_name)
                self.assertIsNone(identity.avatar_url)

    def test_provider_error_redirect_and_malformed_response_cannot_create_identity(self):
        for body, status in (
            ({"error": "invalid_grant", "error_description": "synthetic-secret"}, 400),
            ({"id_token": self.token()}, 302),
            ({}, 200), ({"id_token": 42}, 200), ([], 200),
        ):
            with self.subTest(status=status, body_type=type(body)), self.network(
                token_body=body, token_status=status,
            ):
                with self.assertRaises(GoogleLoginError) as raised:
                    self.exchange()
                self.assertNotIn("synthetic-secret", str(raised.exception))

    def test_certificate_fetch_failure_is_sanitized(self):
        with self.network(certs_status=503), self.assertRaises(GoogleLoginError):
            self.exchange()

    def test_network_error_is_sanitized(self):
        with patch.object(
            requests.Session, "request", side_effect=requests.Timeout("synthetic-secret"),
        ), self.assertRaises(GoogleLoginError) as raised:
            self.exchange()
        self.assertNotIn("synthetic-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
