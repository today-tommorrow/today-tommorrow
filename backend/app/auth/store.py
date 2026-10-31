"""Database-only authentication persistence; callers own transaction boundaries."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.models import AuthSession, OAuthLoginState, User


@dataclass(frozen=True)
class IssuedSession:
    id: UUID
    user_id: UUID
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class SessionPrincipal:
    user: User
    session_id: UUID
    expires_at: datetime
    csrf_token: str = field(repr=False)


@dataclass(frozen=True)
class IssuedOAuthState:
    id: UUID
    state: str = field(repr=False)
    browser_binding: str = field(repr=False)
    nonce: str = field(repr=False)
    expires_at: datetime


def _aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("aware_datetime_required")


def _lifetime(now: datetime, expires_at: datetime) -> None:
    _aware(now)
    _aware(expires_at)
    if expires_at <= now:
        raise ValueError("invalid_expiry")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _secret_hash(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _hash(value)
    except UnicodeEncodeError:
        return None


class AuthStore:
    def __init__(self, db: Session, *, signing_key: bytes):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("signing_key_requires_32_bytes")
        self._db = db
        self._signing_key = signing_key

    def save_google_identity(
        self, *, subject: str, email: str, display_name: str | None = None,
        avatar_url: str | None = None, now: datetime,
    ) -> User:
        """Persist an already verified Google identity, keyed exclusively by sub."""
        _aware(now)
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("google_subject_required")
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email_required")
        statement = insert(User).values(
            google_sub=subject, email=email, display_name=display_name, avatar_url=avatar_url,
            created_at=now, updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[User.google_sub],
            set_={
                "email": statement.excluded.email,
                "display_name": statement.excluded.display_name,
                "avatar_url": statement.excluded.avatar_url,
                "updated_at": statement.excluded.updated_at,
            },
        ).returning(User)
        return self._db.scalars(statement.execution_options(populate_existing=True)).one()

    def _csrf(self, token: str) -> str:
        return hmac.new(self._signing_key, token.encode("ascii"), hashlib.sha256).hexdigest()

    def issue_session(self, user: User, *, now: datetime, expires_at: datetime) -> IssuedSession:
        _lifetime(now, expires_at)
        token = secrets.token_urlsafe(32)
        csrf = self._csrf(token)
        row = AuthSession(
            user_id=user.id, token_hash=_hash(token), csrf_token_hash=_hash(csrf),
            created_at=now, expires_at=expires_at,
        )
        self._db.add(row)
        self._db.flush()
        return IssuedSession(row.id, row.user_id, token, csrf, row.expires_at)

    def get_session(self, token: str | None, *, now: datetime) -> SessionPrincipal | None:
        _aware(now)
        token_hash = _secret_hash(token)
        if not isinstance(token, str) or token_hash is None:
            return None
        result = self._db.execute(select(AuthSession, User).join(
            User, User.id == AuthSession.user_id,
        ).where(
            AuthSession.token_hash == token_hash,
            AuthSession.created_at <= now, AuthSession.expires_at > now,
            AuthSession.revoked_at.is_(None),
        ).execution_options(populate_existing=True)).one_or_none()
        if result is None:
            return None
        row, user = result
        csrf = self._csrf(token)
        if not hmac.compare_digest(row.csrf_token_hash, _hash(csrf)):
            return None
        return SessionPrincipal(user, row.id, row.expires_at, csrf)

    def verify_csrf(self, token: str | None, csrf: str | None, *, now: datetime) -> bool:
        principal = self.get_session(token, now=now)
        csrf_hash = _secret_hash(csrf)
        return principal is not None and csrf_hash is not None and hmac.compare_digest(
            _hash(principal.csrf_token), csrf_hash,
        )

    def revoke_session(self, token: str | None, *, now: datetime) -> bool:
        _aware(now)
        token_hash = _secret_hash(token)
        if token_hash is None:
            return False
        statement = update(AuthSession).where(
            AuthSession.token_hash == token_hash, AuthSession.created_at <= now,
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=now).returning(AuthSession.id)
        return self._db.scalar(statement) is not None

    def issue_oauth_state(self, *, now: datetime, expires_at: datetime) -> IssuedOAuthState:
        _lifetime(now, expires_at)
        state, browser_binding, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        row = OAuthLoginState(
            state_hash=_hash(state), browser_binding_hash=_hash(browser_binding),
            nonce_hash=_hash(nonce), created_at=now, expires_at=expires_at,
        )
        self._db.add(row)
        self._db.flush()
        return IssuedOAuthState(row.id, state, browser_binding, nonce, row.expires_at)

    def consume_oauth_state(
        self, state: str | None, browser_binding: str | None, *, now: datetime,
    ) -> OAuthLoginState | None:
        _aware(now)
        state_hash, binding_hash = _secret_hash(state), _secret_hash(browser_binding)
        if state_hash is None or binding_hash is None:
            return None
        statement = update(OAuthLoginState).where(
            OAuthLoginState.state_hash == state_hash,
            OAuthLoginState.browser_binding_hash == binding_hash,
            OAuthLoginState.consumed_at.is_(None),
            OAuthLoginState.created_at <= now, OAuthLoginState.expires_at > now,
        ).values(consumed_at=now).returning(OAuthLoginState)
        return self._db.scalars(statement.execution_options(populate_existing=True)).one_or_none()

    @staticmethod
    def nonce_matches(record: OAuthLoginState, nonce: str | None) -> bool:
        nonce_hash = _secret_hash(nonce)
        return nonce_hash is not None and hmac.compare_digest(record.nonce_hash, nonce_hash)
