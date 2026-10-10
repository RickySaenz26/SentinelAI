from fastapi import APIRouter

from app.api.v1 import (
    account_recovery,
    assets,
    control_reviews,
    evidence,
    health,
    me,
    memberships,
    organizations,
    roles,
    security_audit,
    session,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(assets.router, tags=["assets"])
api_router.include_router(evidence.router, tags=["evidence"])
api_router.include_router(control_reviews.router, tags=["control-reviews"])
api_router.include_router(session.router, tags=["session"])
api_router.include_router(account_recovery.router, tags=["account-recovery"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(organizations.router, tags=["organizations"])
api_router.include_router(memberships.router, tags=["memberships"])
api_router.include_router(roles.router, tags=["roles"])
api_router.include_router(security_audit.router, tags=["security-audit"])
