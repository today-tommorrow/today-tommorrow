"""Authenticated profile requests use the same session and CSRF store as logout."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.routes import SESSION_COOKIE, AuthError, settings
from app.auth.store import AuthStore, SessionPrincipal
from app.db import get_session

DB = Annotated[Session, Depends(get_session)]


def authenticated(request: Request, db: DB) -> SessionPrincipal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        raise AuthError(401, "unauthenticated")
    config = settings()
    try:
        principal = AuthStore(db, signing_key=config.signing_key).get_session(
            token, now=datetime.now(UTC)
        )
        if principal is None:
            raise AuthError(401, "unauthenticated")
        return principal
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None


def authenticated_write(
    request: Request,
    db: DB,
    principal: Annotated[SessionPrincipal, Depends(authenticated)],
) -> SessionPrincipal:
    config = settings()
    origin, csrf = request.headers.get("origin"), request.headers.get("x-csrf-token")
    if (origin is not None and origin != config.origin) or not csrf or len(csrf) > 128:
        raise AuthError(403, "invalid_csrf")
    try:
        if not AuthStore(db, signing_key=config.signing_key).verify_csrf(
            request.cookies.get(SESSION_COOKIE), csrf, now=datetime.now(UTC)
        ):
            raise AuthError(403, "invalid_csrf")
        return principal
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None
