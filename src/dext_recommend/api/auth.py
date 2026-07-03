"""Anonymous identity, bearer/cookie auth, and CSRF checks."""
from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiohttp import web

from dext_recommend.api.keys import REPOSITORY_KEY, SETTINGS_KEY
from dext_recommend.api.middleware import ApiError
from dext_recommend.app_state.models import utcnow
from dext_recommend.app_state.repositories import AppStateRepository
from dext_recommend.ports import ViewerPermissions

ANON_COOKIE = "scho_anonymous"
CSRF_COOKIE = "scho_csrf"


@dataclass(frozen=True, slots=True)
class Principal:
    owner_id: uuid.UUID
    auth_kind: str
    include_contacts: bool = False
    can_view_review: bool = False
    diagnostics: bool = False

    def viewer_permissions(self) -> ViewerPermissions:
        return ViewerPermissions(
            include_contacts=self.include_contacts,
            can_view_review=self.can_view_review,
            diagnostics=self.diagnostics,
        )


def token_digest(token: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        token.encode("utf-8"),
        "sha256",
    ).hexdigest()


async def create_anonymous_identity(request: web.Request) -> tuple[dict, str, str]:
    settings = request.app[SETTINGS_KEY]
    repo: AppStateRepository = request.app[REPOSITORY_KEY]
    owner_id = uuid.uuid4()
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(seconds=settings.anonymous_token_ttl_seconds)
    await repo.create_identity(
        owner_id=str(owner_id),
        kind="anonymous",
        token_digest=token_digest(token, settings.auth_token_pepper.get_secret_value()),
        expires_at=expires_at,
    )
    return {
        "owner_id": str(owner_id),
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "csrf_token": csrf,
    }, token, csrf


async def require_principal(request: web.Request) -> Principal:
    principal = await optional_principal(request)
    if principal is None:
        raise ApiError(401, "unauthorized", "authentication required")
    if principal.auth_kind == "cookie" and request.method not in {"GET", "HEAD", "OPTIONS"}:
        _check_csrf(request)
    return principal


async def optional_principal(request: web.Request) -> Principal | None:
    settings = request.app[SETTINGS_KEY]
    repo: AppStateRepository = request.app[REPOSITORY_KEY]
    pepper = settings.auth_token_pepper.get_secret_value()
    bearer = _bearer_token(request)
    cookie = request.cookies.get(ANON_COOKIE)
    bearer_principal = await _principal_for_token(repo, bearer, pepper, "bearer") if bearer else None
    cookie_principal = await _principal_for_token(repo, cookie, pepper, "cookie") if cookie else None
    if bearer_principal and cookie_principal and bearer_principal.owner_id != cookie_principal.owner_id:
        raise ApiError(401, "credential_owner_mismatch", "credentials do not match")
    return bearer_principal or cookie_principal


async def _principal_for_token(
    repo: AppStateRepository,
    token: str | None,
    pepper: str,
    auth_kind: str,
) -> Principal | None:
    if not token:
        return None
    row = await repo.get_identity_by_digest(token_digest(token, pepper))
    if row is None or row.revoked_at is not None or _as_utc(row.expires_at) <= utcnow():
        raise ApiError(401, "invalid_credential", "credential is invalid or expired")
    return Principal(owner_id=uuid.UUID(row.owner_id), auth_kind=auth_kind)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bearer_token(request: web.Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "invalid_authorization", "Authorization must be Bearer")
    return token.strip()


def _check_csrf(request: web.Request) -> None:
    settings = request.app[SETTINGS_KEY]
    origin = request.headers.get("Origin")
    if settings.csrf_allowed_origins and origin not in settings.csrf_allowed_origins:
        raise ApiError(403, "csrf_origin_forbidden", "Origin is not allowed")
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("X-CSRF-Token")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise ApiError(403, "csrf_token_invalid", "CSRF token is missing or invalid")


def set_identity_cookies(response: web.Response, token: str, csrf: str, request: web.Request) -> None:
    settings = request.app[SETTINGS_KEY]
    response.set_cookie(ANON_COOKIE, token, **settings.cookie_kwargs)
    csrf_kwargs = dict(settings.cookie_kwargs)
    csrf_kwargs["httponly"] = False
    response.set_cookie(CSRF_COOKIE, csrf, **csrf_kwargs)


__all__ = [
    "ANON_COOKIE",
    "CSRF_COOKIE",
    "Principal",
    "create_anonymous_identity",
    "optional_principal",
    "require_principal",
    "set_identity_cookies",
    "token_digest",
]
