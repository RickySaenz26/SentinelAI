"""Tenant hierarchy, real PostgreSQL optimistic locking and owner invariants."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.organizations import current_organization
from app.authorization.membership_policy import (
    assert_membership_change_allowed,
    resolve_assignable_role,
)
from app.authorization.policy import ActorContext
from app.core.errors import ApplicationError
from app.main import app
from app.platform.database.models import (
    Membership,
    Organization,
    OutboxEvent,
    Role,
    SecurityAuditEvent,
)
from app.platform.database.session import set_organization_context


def actor_for(seeded, code="org_owner"):
    return ActorContext(
        user_id=seeded["users"][code],
        organization_id=seeded["org_a"],
        membership_id=seeded["memberships"][code],
        session_id=uuid4(),
        role_code=code,
        permissions=frozenset(
            {"membership:invite", "membership:update", "membership:revoke", "organization:update"}
        ),
        csrf_token="unused",
    )


def member_url(seeded, role="viewer"):
    return f"/api/v1/organizations/{seeded['org_a']}/memberships/{seeded['memberships'][role]}"


def counts(admin_engine):
    with Session(admin_engine) as session:
        return (
            session.scalar(select(func.count()).select_from(SecurityAuditEvent)),
            session.scalar(select(func.count()).select_from(OutboxEvent)),
        )


@pytest.mark.parametrize(
    "actor_role,target",
    [
        ("platform_admin", "platform_admin"),
        ("org_owner", "platform_admin"),
        ("security_manager", "platform_admin"),
        ("security_manager", "org_owner"),
        ("security_manager", "security_manager"),
        ("analyst", "viewer"),
        ("viewer", "viewer"),
        ("auditor", "viewer"),
    ],
)
@pytest.mark.parametrize("operation", ["status", "role", "delete"])
def test_hierarchy_denies_existing_target_even_with_allowed_new_role(
    client, login, seeded, admin_engine, actor_role, target, operation
):
    auth = login(actor_role)
    headers = {**auth["headers"], "If-Match": "1"}
    initial = counts(admin_engine)
    if operation == "delete":
        response = client.delete(member_url(seeded, target), headers=headers)
    else:
        body = {"status": "suspended"} if operation == "status" else {"role_code": "viewer"}
        response = client.patch(member_url(seeded, target), json=body, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"
    with Session(admin_engine) as session:
        member = session.get(Membership, seeded["memberships"][target])
        assert member.status == "active" and member.version == 1 and member.deleted_at is None
        assert member.role_id == seeded["roles"][target]
        denial = session.scalar(
            select(SecurityAuditEvent).where(
                SecurityAuditEvent.action == "membership.change_denied"
            )
        )
        assert denial.outcome == "denied"
        assert denial.details == {
            "operation": "revoke" if operation == "delete" else "update",
            "reason": "authorization_policy",
        }
    assert counts(admin_engine) == (initial[0] + 1, initial[1])


@pytest.mark.parametrize("actor_role", ["platform_admin", "org_owner", "security_manager"])
def test_reserved_new_role_rejected_on_create_and_update(client, login, seeded, actor_role):
    auth = login(actor_role)
    base = f"/api/v1/organizations/{seeded['org_a']}/memberships"
    response = client.post(
        base,
        headers=auth["headers"],
        json={"email": "unlisted@example.com", "role_code": "platform_admin"},
    )
    assert response.status_code == 403
    response = client.patch(
        member_url(seeded),
        headers={**auth["headers"], "If-Match": "1"},
        json={"role_code": "platform_admin"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "actor_role,assigned",
    [
        ("platform_admin", "org_owner"),
        ("org_owner", "security_manager"),
        ("org_owner", "org_owner"),
        ("security_manager", "analyst"),
        ("security_manager", "viewer"),
        ("security_manager", "auditor"),
    ],
)
def test_permitted_create_update_suspend_reactivate_and_revoke(
    client, login, seeded, create_user, admin_engine, actor_role, assigned
):
    target = create_user(role=None, organization_id=seeded["org_a"])
    auth = login(actor_role)
    base = f"/api/v1/organizations/{seeded['org_a']}/memberships"
    body = {"email": target["email"], "role_code": assigned}
    response = client.post(base, json=body, headers=auth["headers"])
    assert response.status_code == 201, response.text
    member = response.json()
    assert member["version"] == 1 and member["role_code"] == assigned
    url = f"{base}/{member['id']}"
    for expected, changes in [
        (1, {"status": "suspended"}),
        (2, {"status": "active"}),
        (3, {"role_code": "viewer"}),
    ]:
        response = client.patch(
            url, json=changes, headers={**auth["headers"], "If-Match": str(expected)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == expected + 1
    response = client.delete(url, headers={**auth["headers"], "If-Match": "4"})
    assert response.status_code == 204
    response = client.delete(url, headers={**auth["headers"], "If-Match": "5"})
    assert response.status_code == 404
    with Session(admin_engine) as session:
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == member["id"])
        ).all()
        assert len(events) == 5
        assert len({item.idempotency_key for item in events}) == 5
        assert all(item.idempotency_key for item in events)


def test_membership_invalid_missing_duplicate_and_cross_tenant(client, login, seeded):
    auth = login()
    base = f"/api/v1/organizations/{seeded['org_a']}/memberships"
    assert client.get(base).status_code == 200
    assert len(client.get(base).json()["items"]) == 6
    response = client.post(
        base, headers=auth["headers"], json={"email": "missing@example.com", "role_code": "viewer"}
    )
    assert response.status_code == 422
    for code in ("viewer", "org_owner"):
        response = client.post(
            base,
            headers=auth["headers"],
            json={"email": seeded["emails"][code], "role_code": "viewer"},
        )
        assert response.status_code == 409
    headers = {**auth["headers"], "If-Match": "1"}
    assert client.patch(member_url(seeded), json={}, headers=headers).status_code == 422
    for ident in (uuid4(), seeded["owner_b_membership"]):
        for method in ("patch", "delete"):
            kwargs = {"json": {"status": "suspended"}} if method == "patch" else {}
            assert (
                getattr(client, method)(f"{base}/{ident}", headers=headers, **kwargs).status_code
                == 404
            )
    assert (
        client.post(
            f"/api/v1/organizations/{seeded['org_b']}/memberships",
            headers=auth["headers"],
            json={"email": "none@example.com", "role_code": "viewer"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize("operation", ["downgrade", "suspend", "delete"])
def test_last_owner_protected(client, login, seeded, admin_engine, operation):
    auth = login("platform_admin")
    headers = {**auth["headers"], "If-Match": "1"}
    before = counts(admin_engine)
    if operation == "delete":
        response = client.delete(member_url(seeded, "org_owner"), headers=headers)
    else:
        body = {"role_code": "viewer"} if operation == "downgrade" else {"status": "suspended"}
        response = client.patch(member_url(seeded, "org_owner"), json=body, headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "LAST_OWNER_PROTECTED"
    assert counts(admin_engine) == before


def test_organization_reads_updates_and_scope(client, login, seeded, admin_engine):
    auth = login()
    base = f"/api/v1/organizations/{seeded['org_a']}"
    assert client.get("/api/v1/organizations").json()["items"][0]["id"] == str(seeded["org_a"])
    assert client.get(base).json()["version"] == 1
    assert client.get(f"/api/v1/organizations/{seeded['org_b']}").status_code == 404
    assert client.get(f"/api/v1/organizations/{uuid4()}").status_code == 404
    headers = {**auth["headers"], "If-Match": "1"}
    response = client.patch(base, json={"name": "Renamed organization"}, headers=headers)
    assert response.status_code == 200 and response.json()["version"] == 2
    before = counts(admin_engine)
    response = client.patch(base, json={"name": "Stale update"}, headers=headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert counts(admin_engine) == before


@pytest.mark.parametrize("target_kind", ["organization", "membership", "membership_delete"])
def test_concurrent_if_match_has_one_winner(login, seeded, admin_engine, target_kind):
    barrier = Barrier(2)
    with (
        TestClient(app, base_url="https://testserver") as first,
        TestClient(app, base_url="https://testserver") as second,
    ):
        auths = [login(client=first), login(client=second)]
        before = counts(admin_engine)

        def mutate(index):
            auth = auths[index]
            headers = {**auth["headers"], "If-Match": "1"}
            barrier.wait(timeout=10)
            if target_kind == "organization":
                return auth["client"].patch(
                    f"/api/v1/organizations/{seeded['org_a']}",
                    headers=headers,
                    json={"name": f"Concurrent name {index}"},
                )
            if target_kind == "membership_delete":
                return auth["client"].delete(member_url(seeded), headers=headers)
            return auth["client"].patch(
                member_url(seeded),
                headers=headers,
                json={"role_code": "auditor" if index else "analyst"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(mutate, range(2)))
    successful = 204 if target_kind == "membership_delete" else 200
    assert sorted(item.status_code for item in responses) == [successful, 409], [
        r.text for r in responses
    ]
    loser = next(item for item in responses if item.status_code == 409)
    assert loser.json()["error"]["code"] == "VERSION_CONFLICT"
    assert counts(admin_engine) == (before[0] + 1, before[1] + 1)


def test_concurrent_last_owner_preserves_one(login, seeded, create_user, admin_engine):
    extra = create_user(role="org_owner", organization_id=seeded["org_a"])
    targets = [seeded["memberships"]["org_owner"], extra["membership_id"]]
    barrier = Barrier(2)
    with (
        TestClient(app, base_url="https://testserver") as first,
        TestClient(app, base_url="https://testserver") as second,
    ):
        auths = [login("platform_admin", client=first), login("platform_admin", client=second)]

        def mutate(index):
            auth = auths[index]
            barrier.wait(timeout=10)
            return auth["client"].patch(
                f"/api/v1/organizations/{seeded['org_a']}/memberships/{targets[index]}",
                headers={**auth["headers"], "If-Match": "1"},
                json={"role_code": "viewer"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(mutate, range(2)))
    assert sorted(item.status_code for item in responses) == [200, 409], [r.text for r in responses]
    assert (
        next(r for r in responses if r.status_code == 409).json()["error"]["code"]
        == "LAST_OWNER_PROTECTED"
    )
    with Session(admin_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.organization_id == seeded["org_a"],
                    Membership.role_id == seeded["roles"]["org_owner"],
                    Membership.status == "active",
                    Membership.deleted_at.is_(None),
                )
            )
            == 1
        )


def test_domain_rejects_invalid_scope_action_missing_role_and_future_role(seeded):
    actor = actor_for(seeded)
    args = {"action": "create", "organization_id": seeded["org_a"], "target_user_id": uuid4()}
    with pytest.raises(ApplicationError, match="ORGANIZATION_NOT_FOUND") as error:
        assert_membership_change_allowed(actor, **{**args, "organization_id": seeded["org_b"]})
    assert error.value.code == "ORGANIZATION_NOT_FOUND"
    assert error.value.status_code == 404
    with pytest.raises(ValueError):
        assert_membership_change_allowed(actor, **{**args, "action": "unknown"})
    with pytest.raises(ApplicationError):
        assert_membership_change_allowed(actor, **args)
    with pytest.raises(ApplicationError):
        assert_membership_change_allowed(actor, **args, new_role_code="future_global_role")


def test_tenant_role_from_other_org_is_not_resolved(db, seeded, admin_engine):
    with Session(admin_engine) as session:
        global_viewer = session.get(Role, seeded["roles"]["viewer"])
        global_viewer.code = "viewer_reserved_for_test"
        session.add(
            Role(
                code="viewer",
                name="Other tenant viewer",
                is_system=False,
                organization_id=seeded["org_b"],
            )
        )
        session.commit()
    try:
        set_organization_context(db, seeded["org_a"])
        with pytest.raises(ApplicationError) as error:
            resolve_assignable_role(db, actor_for(seeded), seeded["org_a"], "viewer")
        assert error.value.code == "ROLE_NOT_FOUND"
    finally:
        db.rollback()
        with Session(admin_engine) as session:
            session.get(Role, seeded["roles"]["viewer"]).code = "viewer"
            session.commit()


@pytest.mark.parametrize("state", ["missing", "suspended", "deleted"])
def test_current_organization_rejects_inactive_and_missing(db, admin_engine, seeded, state):
    actor = actor_for(seeded)
    if state == "missing":
        actor = ActorContext(
            **{field: getattr(actor, field) for field in actor.__dataclass_fields__}
            | {"organization_id": uuid4()}
        )
    else:
        with Session(admin_engine) as session:
            organization = session.get(Organization, seeded["org_a"])
            if state == "suspended":
                organization.status = "suspended"
            else:
                organization.deleted_at = datetime.now(UTC)
            session.commit()
    with pytest.raises(ApplicationError) as error:
        current_organization(db, actor, actor.organization_id)
    assert error.value.status_code == 404
