"""Exchange a server-side authorization code for a verified Google identity."""

from dataclasses import dataclass, field
from functools import partial
from urllib.parse import urlsplit

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

TOKEN_URL = "https://oauth2.googleapis.com/token"
TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    display_name: str | None
    avatar_url: str | None
    nonce: str = field(repr=False)


class GoogleLoginError(Exception):
    """Public failure deliberately excludes Google's response and credentials."""


def _profile_string(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


def _avatar(value: object) -> str | None:
    result = _profile_string(value, limit=2048)
    if result is None:
        return None
    try:
        url = urlsplit(result)
        if url.scheme == "https" and url.hostname and not url.username and not url.password:
            return result
    except ValueError:
        pass
    return None


def exchange_identity(
    *, code: str, client_id: str, client_secret: str, redirect_uri: str,
) -> GoogleIdentity:
    """Verify signature/issuer/audience/time; the caller must match the saved nonce."""
    try:
        if not all(isinstance(value, str) and value.strip() for value in (
            code, client_id, client_secret, redirect_uri,
        )):
            raise ValueError("missing_oauth_parameters")
        with requests.Session() as session:
            with session.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code", "code": code,
                    "client_id": client_id, "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                timeout=TIMEOUT_SECONDS, allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise ValueError("token_exchange_failed")
                body = response.json()
            if not isinstance(body, dict):
                raise ValueError("invalid_token_response")
            token = body.get("id_token")
            if not isinstance(token, str) or not token:
                raise ValueError("missing_id_token")
            request = partial(
                Request(session=session), timeout=TIMEOUT_SECONDS, allow_redirects=False,
            )
            claims = id_token.verify_oauth2_token(token, request, audience=client_id)
        subject, email, nonce = (claims.get(name) for name in ("sub", "email", "nonce"))
        if (
            not isinstance(subject, str) or not subject.strip()
            or not isinstance(email, str) or not email.strip()
            or not isinstance(nonce, str) or not nonce.strip()
        ):
            raise ValueError("invalid_identity_claims")
        if claims.get("email_verified") is not True:
            raise ValueError("unverified_email")
        return GoogleIdentity(
            subject=subject, email=email, nonce=nonce,
            display_name=_profile_string(claims.get("name"), limit=512),
            avatar_url=_avatar(claims.get("picture")),
        )
    except (requests.RequestException, GoogleAuthError, ValueError, KeyError, TypeError):
        raise GoogleLoginError("google_login_failed") from None
