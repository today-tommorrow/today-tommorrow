"""Backend-only auth settings; never infer the callback from an incoming Host."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AuthSettings:
    origin: str
    redirect_uri: str
    signing_key: bytes = field(repr=False)
    client_id: str
    client_secret: str = field(repr=False)
    session_seconds: int
    state_seconds: int

    @property
    def secure(self) -> bool:
        return self.origin.startswith("https://")

    @classmethod
    def load(cls, *, require_google: bool = False) -> AuthSettings:
        origin = os.environ.get("PUBLIC_ORIGIN", "http://127.0.0.1:5173")
        url = urlsplit(origin)
        if (
            not url.hostname or url.username or url.password or url.path
            or url.query or url.fragment or url.netloc != url.netloc.strip()
            or any(c.isspace() for c in origin)
            or not (url.scheme == "https" or origin == "http://127.0.0.1:5173")
        ):
            raise ValueError("invalid_public_origin")
        # Force malformed ports to fail before a redirect or cookie is issued.
        _ = url.port
        redirect = os.environ.get("GOOGLE_REDIRECT_URI", origin + "/api/auth/google/callback")
        if redirect != origin + "/api/auth/google/callback":
            raise ValueError("callback_origin_mismatch")
        key = os.environ.get("SESSION_SECRET", "").encode("utf-8")
        if len(key) < 32:
            raise ValueError("session_secret_requires_32_bytes")
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        if require_google and (not client_id or not client_secret):
            raise ValueError("google_configuration_required")
        session_seconds = int(os.environ.get("SESSION_TTL_SECONDS", "28800"))
        state_seconds = int(os.environ.get("OAUTH_STATE_TTL_SECONDS", "600"))
        if not (0 < session_seconds <= 31_536_000 and 0 < state_seconds <= 3600):
            raise ValueError("invalid_auth_lifetime")
        return cls(origin, redirect, key, client_id, client_secret, session_seconds, state_seconds)
