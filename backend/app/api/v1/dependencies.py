import hmac
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.authorization.membership_policy import lock_organization
from app.authorization.policy import ActorContext
from app.core.config import get_settings
from app.core.errors import ApplicationError
from app.platform.crypto import hash_token
from app.platform.database.models import (
    Membership,
    Organization,
    Permission,
    Role,
    RolePermission,
    User,
    UserSession,
)
from app.platform.database.session import get_db_session, set_organization_context, set_user_context

SESSION_COOKIE = "__Host-sentinel_session"


def get_actor(
    request: Request,
    csrf_token: str | None = Header(default=None, alias=get_settings().csrf_header_name),
    session: Session = Depends(get_db_session),
) -> ActorContext:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if raw_token is None:
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    now = datetime.now(UTC)
    user_session = session.scalar(
        select(UserSession).where(
            UserSession.token_hash == hash_token(raw_token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.idle_expires_at > now,
        )
    )
    if user_session is None:
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    # A switch touches two tenants. Serialize switches before either tenant lock,
    # so simultaneous A -> B and B -> A never acquire the locks in reverse order.
    if request.url.path == "/api/v1/me/active-organization":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('sentinelai-org-switch', 3))")
        )
    lock_organization(session, user_session.organization_id)
    session.refresh(user_session)
    now = datetime.now(UTC)
    if (
        user_session.revoked_at is not None
        or user_session.expires_at <= now
        or user_session.idle_expires_at <= now
    ):
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    user = session.get(User, user_session.user_id)
    if user is None or user.deleted_at is not None or user.status != "active":
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    set_user_context(session, user_session.user_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.id == user_session.membership_id,
            Membership.user_id == user_session.user_id,
            Membership.organization_id == user_session.organization_id,
            Membership.status == "active",
            Membership.deleted_at.is_(None),
        )
    )
    if membership is None:
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    set_organization_context(session, user_session.organization_id)
    organization = session.get(Organization, user_session.organization_id)
    if (
        organization is None
        or organization.deleted_at is not None
        or organization.status != "active"
    ):
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    permissions = set(
        session.scalars(
            select(RolePermission.permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Role.id == membership.role_id)
        ).all()
    )
    permission_codes = set(
        session.scalars(
            select(Permission.code)
            .join(RolePermission)
            .where(RolePermission.permission_id.in_(permissions))
        ).all()
    )
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        settings = get_settings()
        if csrf_token is None or not hmac.compare_digest(
            hash_token(csrf_token), user_session.csrf_secret_hash
        ):
            raise ApplicationError("CSRF_VALIDATION_FAILED", "Solicitud no válida.", 403)
        if origin is not None and origin not in settings.trusted_origin_list:
            raise ApplicationError("CSRF_VALIDATION_FAILED", "Solicitud no válida.", 403)
    user_session.last_seen_at = now
    user_session.idle_expires_at = min(
        now + timedelta(minutes=get_settings().session_idle_minutes), user_session.expires_at
    )
    session.flush()
    role = session.get(Role, membership.role_id)
    if role is None:
        raise ApplicationError("UNAUTHENTICATED", "Autenticación requerida.", 401)
    return ActorContext(
        user_id=user_session.user_id,
        organization_id=user_session.organization_id,
        membership_id=membership.id,
        session_id=user_session.id,
        role_code=role.code,
        permissions=frozenset(permission_codes),
        csrf_token=csrf_token or "",
    )
