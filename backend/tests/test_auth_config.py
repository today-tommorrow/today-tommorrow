import os
import unittest
from unittest.mock import patch


class AuthConfigTests(unittest.TestCase):
    def test_local_defaults_and_secrets_not_in_repr(self):
        from app.auth.config import AuthSettings
        with patch.dict(os.environ, {"SESSION_SECRET": "s"*32}, clear=True):
            settings = AuthSettings.load()
        self.assertEqual(settings.origin, "http://127.0.0.1:5173")
        self.assertEqual(settings.redirect_uri, settings.origin + "/api/auth/google/callback")
        self.assertEqual(settings.session_seconds, 28800)
        self.assertEqual(settings.state_seconds, 600)
        self.assertFalse(settings.secure)
        self.assertNotIn("s"*32, repr(settings))

    def test_invalid_config_and_google_missing_rejected(self):
        from app.auth.config import AuthSettings
        base = {"SESSION_SECRET": "s"*32}
        for values in ({}, {"SESSION_SECRET": "short"}, {**base, "PUBLIC_ORIGIN": "http://example.com"}, {**base, "PUBLIC_ORIGIN": "https://example.com/path"}, {**base, "GOOGLE_REDIRECT_URI": "https://example.com/callback"}, {**base, "SESSION_TTL_SECONDS": "0"}, {**base, "OAUTH_STATE_TTL_SECONDS": "invalid"}):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ValueError):
                    AuthSettings.load()
        with patch.dict(os.environ, base, clear=True):
            with self.assertRaises(ValueError):
                AuthSettings.load(require_google=True)

    def test_https_cookie_and_callback_match(self):
        from app.auth.config import AuthSettings
        with patch.dict(os.environ, {"SESSION_SECRET": "s"*32, "PUBLIC_ORIGIN": "https://example.com", "GOOGLE_CLIENT_ID": "test-client", "GOOGLE_CLIENT_SECRET": "secret-client"}, clear=True):
            settings = AuthSettings.load(require_google=True)
        self.assertTrue(settings.secure)
        self.assertEqual(settings.redirect_uri, "https://example.com/api/auth/google/callback")
        self.assertNotIn("secret-client", repr(settings))
