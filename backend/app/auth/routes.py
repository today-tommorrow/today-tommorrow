"""Google code flow and opaque service sessions on the existing auth store."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.config import AuthSettings
from app.auth.google import GoogleLoginError, exchange_identity
from app.auth.store import AuthStore
from app.db import get_session

router = APIRouter(prefix="/api/auth")
DB = Annotated[Session, Depends(get_session)]
SESSION_COOKIE = "today_tommorrow_session"
BINDING_COOKIE = "today_tommorrow_oauth_binding"
PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class AuthError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


def auth_error_handler(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, AuthError)
    messages = {
        "unauthenticated": "로그인이 필요합니다.",
        "auth_unavailable": "로그인 서비스를 사용할 수 없습니다.",
        "invalid_oauth": "로그인을 다시 시작해주세요.",
        "invalid_csrf": "로그인 상태를 다시 확인해주세요.",
    }
    response = JSONResponse(
        {"code": error.code, "message": messages[error.code], "request_id": uuid4().hex},
        status_code=error.status, headers=PRIVATE,
    )
    if error.status == 401:
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
    return response


def settings(*, google: bool = False) -> AuthSettings:
    try:
        return AuthSettings.load(require_google=google)
    except ValueError:
        raise AuthError(503, "auth_unavailable") from None


def cookie(response: Response, name: str, value: str, ttl: int, config: AuthSettings) -> None:
    response.set_cookie(name, value, max_age=ttl, httponly=True, secure=config.secure, samesite="lax", path="/")


def redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers=PRIVATE)


@router.get("/google/start")
def google_start(db: DB):
    config = settings(google=True)
    now = datetime.now(UTC)
    try:
        issued = AuthStore(db, signing_key=config.signing_key).issue_oauth_state(
            now=now, expires_at=now + timedelta(seconds=config.state_seconds),
        )
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None
    response = redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": config.client_id, "redirect_uri": config.redirect_uri,
        "response_type": "code", "scope": "openid email profile",
        "state": issued.state, "nonce": issued.nonce,
    }))
    cookie(response, BINDING_COOKIE, issued.browser_binding, config.state_seconds, config)
    return response


@router.get("/google/callback")
def google_callback(request: Request, db: DB):
    params = request.query_params
    # Uvicorn builds its access-log URL from this scope when sending headers.
    # Parse once, then keep authorization codes/state out of that log.
    request.scope["query_string"] = b""
    config = settings(google=True)
    if any(len(params.getlist(key)) > 1 for key in ("state", "code", "error")):
        raise AuthError(400, "invalid_oauth")
    state, code, error = params.get("state"), params.get("code"), params.get("error")
    binding = request.cookies.get(BINDING_COOKIE)
    if (
        not state or len(state) > 128 or not binding or len(binding) > 128
        or (code is not None and len(code) > 4096)
        or (error is not None and len(error) > 128)
    ):
        raise AuthError(400, "invalid_oauth")
    store = AuthStore(db, signing_key=config.signing_key)
    try:
        consumed = store.consume_oauth_state(state, binding, now=datetime.now(UTC))
        if consumed is None:
            raise AuthError(400, "invalid_oauth")
        nonce_hash = consumed.nonce_hash
        db.commit()  # A failed Google exchange must never revive a used state.
        if error == "access_denied" and not code:
            response = redirect(config.origin + "/login?error=oauth_cancelled")
        else:
            if error or not code:
                raise AuthError(400, "invalid_oauth")
            try:
                identity = exchange_identity(
                    code=code, client_id=config.client_id,
                    client_secret=config.client_secret, redirect_uri=config.redirect_uri,
                )
            except GoogleLoginError:
                raise AuthError(400, "invalid_oauth") from None
            if not hmac.compare_digest(nonce_hash, hashlib.sha256(identity.nonce.encode("utf-8")).hexdigest()):
                raise AuthError(400, "invalid_oauth")
            now = datetime.now(UTC)
            user = store.save_google_identity(
                subject=identity.subject, email=identity.email,
                display_name=identity.display_name, avatar_url=identity.avatar_url, now=now,
            )
            store.revoke_session(request.cookies.get(SESSION_COOKIE), now=now)
            issued = store.issue_session(user, now=now, expires_at=now + timedelta(seconds=config.session_seconds))
            db.commit()
            response = redirect(config.origin + "/onboarding")
            cookie(response, SESSION_COOKIE, issued.token, config.session_seconds, config)
        response.delete_cookie(BINDING_COOKIE, httponly=True, secure=config.secure, samesite="lax")
        return response
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None


@router.get("/me")
def me(request: Request, db: DB):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        raise AuthError(401, "unauthenticated")
    config = settings()
    try:
        principal = AuthStore(db, signing_key=config.signing_key).get_session(token, now=datetime.now(UTC))
        if principal is None:
            raise AuthError(401, "unauthenticated")
        user = principal.user
        return JSONResponse({
            "user": {"id": str(user.id), "display_name": user.display_name,
                     "email": user.email, "avatar_url": user.avatar_url},
            "csrf_token": principal.csrf_token, "expires_at": principal.expires_at.isoformat(),
        }, headers=PRIVATE)
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None


@router.post("/logout", status_code=204)
def logout(request: Request, db: DB):
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 128:
        raise AuthError(401, "unauthenticated")
    config = settings()
    try:
        store = AuthStore(db, signing_key=config.signing_key)
        now = datetime.now(UTC)
        if store.get_session(token, now=now) is None:
            raise AuthError(401, "unauthenticated")
        origin = request.headers.get("origin")
        csrf = request.headers.get("x-csrf-token")
        if (origin is not None and origin != config.origin) or not csrf or len(csrf) > 128 or not store.verify_csrf(token, csrf, now=now):
            raise AuthError(403, "invalid_csrf")
        store.revoke_session(token, now=now)
        db.commit()
        response = Response(status_code=204, headers=PRIVATE)
        response.delete_cookie(SESSION_COOKIE, httponly=True, secure=config.secure, samesite="lax")
        return response
    except SQLAlchemyError:
        db.rollback()
        raise AuthError(503, "auth_unavailable") from None
