"""Real PostgreSQL fixtures restricted to the ephemeral quality project."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.assets.policy import LabPolicy
from app.assets.publisher import PolicyPublisher
from app.core.config import get_settings
from app.core.rate_limit import reset_rate_limits
from app.main import app
from app.platform.crypto import hash_password
from app.platform.database.models import (
    Membership,
    Organization,
    PasswordCredential,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.platform.database.session import dispose_engine_for_tests, get_engine

PASSWORD = "Closure test password 42!"


@pytest.fixture(scope="session")
def publisher_engine(admin_engine):
    name, password = "policy_test_" + uuid4().hex, uuid4().hex
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE ROLE {name} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOBYPASSRLS INHERIT"
        )
        connection.exec_driver_sql(f"GRANT sentinelai_policy_publisher TO {name}")
    engine = create_engine(admin_engine.url.set(username=name, password=password))
    yield engine
    engine.dispose()
    with admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM policy_publisher_tenants WHERE role_name=:name"), {"name": name}
        )
        connection.exec_driver_sql(f"DROP ROLE {name}")


@pytest.fixture
def publish_policy(admin_engine, publisher_engine, seeded):
    import json

    with admin_engine.begin() as connection:
        for org in (seeded["org_a"], seeded["org_b"]):
            connection.execute(
                text("INSERT INTO policy_publisher_tenants VALUES(:role,:org)"),
                {"role": publisher_engine.url.username, "org": org},
            )

    def publish(values, *, organization_id=None):
        publisher = PolicyPublisher(
            sessionmaker(publisher_engine), organization_id or seeded["org_a"]
        )
        policy = LabPolicy.model_validate_json(json.dumps(values))
        return publisher.publish(
            policy,
            expected_sequence=publisher.inspect()["sequence"],
            publication_id=uuid4(),
            provenance="synthetic-fixture",
        )

    return publish


@pytest.fixture(scope="session")
def admin_engine():
    url = os.environ["TEST_ADMIN_DATABASE_URL"]
    if not (make_url(url).database or "").startswith("closure_test"):
        raise RuntimeError("Refusing destructive fixtures outside closure_test database")
    engine = create_engine(url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine():
    engine = get_engine()
    if not (engine.url.database or "").startswith("closure_test"):
        raise RuntimeError("Runtime tests require closure_test database")
    yield engine
    dispose_engine_for_tests()


@pytest.fixture(scope="session")
def engine(runtime_engine):
    return runtime_engine


@pytest.fixture(autouse=True)
def clean_database(admin_engine, reference_rows):
    reset_rate_limits()
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE account_recovery_tokens, sessions, security_audit_events, "
                "outbox_events, password_credentials, memberships, organizations, users CASCADE"
            )
        )
        for table, rows in reference_rows:
            if table.name == "roles" or table.name == "role_permissions":
                connection.execute(table.insert(), rows)
    yield
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def reference_rows(admin_engine):
    with admin_engine.connect() as connection:
        rows = [
            (table, [dict(row) for row in connection.execute(table.select()).mappings()])
            for table in (Role.__table__, Permission.__table__, RolePermission.__table__)
        ]
    if not rows[0][1]:
        raise RuntimeError("Seed roles missing; initialize a fresh ephemeral database")
    return rows


@pytest.fixture
def db(runtime_engine):
    with Session(runtime_engine, expire_on_commit=False, autoflush=False) as session:
        yield session
        session.rollback()


@pytest.fixture
def client():
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture(scope="session")
def password_hash():
    return hash_password(PASSWORD)


@pytest.fixture
def create_user(admin_engine, password_hash):
    def create(*, role="viewer", organization_id, email=None):
        with Session(admin_engine, expire_on_commit=False) as session:
            user = User(email=email or f"test-{uuid4().hex}@example.com", display_name="Test user")
            session.add(user)
            session.flush()
            session.add(PasswordCredential(user_id=user.id, password_hash=password_hash))
            membership_id = None
            if role is not None:
                role_row = session.scalar(select(Role).where(Role.code == role, Role.is_system))
                member = Membership(
                    organization_id=organization_id, user_id=user.id, role_id=role_row.id
                )
                session.add(member)
                session.flush()
                membership_id = member.id
            session.commit()
            return {
                "id": user.id,
                "user_id": user.id,
                "email": user.email,
                "password": PASSWORD,
                "membership_id": membership_id,
                "organization_id": organization_id,
            }

    return create


@pytest.fixture
def seeded(admin_engine, create_user):
    org_a, org_b = uuid4(), uuid4()
    with Session(admin_engine) as session:
        session.add_all(
            [
                Organization(id=org_a, name="Org A", slug=f"org-a-{org_a.hex}"),
                Organization(id=org_b, name="Org B", slug=f"org-b-{org_b.hex}"),
            ]
        )
        session.commit()
        roles = dict(session.execute(select(Role.code, Role.id)).all())
    users, memberships, emails = {}, {}, {}
    for code in ("platform_admin", "org_owner", "security_manager", "analyst", "viewer", "auditor"):
        user = create_user(role=code, organization_id=org_a)
        users[code], memberships[code], emails[code] = (
            user["id"],
            user["membership_id"],
            user["email"],
        )
    with Session(admin_engine) as session:
        member = Membership(
            organization_id=org_b, user_id=users["org_owner"], role_id=roles["viewer"]
        )
        session.add(member)
        session.flush()
        owner_b_membership = member.id
        session.commit()
    return {
        "org_a": org_a,
        "org_b": org_b,
        "users": users,
        "memberships": memberships,
        "emails": emails,
        "password": PASSWORD,
        "roles": roles,
        "owner_b_membership": owner_b_membership,
    }


@pytest.fixture
def login(seeded, client):
    def authenticate(role="org_owner", client=None):
        active_client = client or default_client
        response = active_client.post(
            "/api/v1/session",
            json={"email": seeded["emails"][role], "password": seeded["password"]},
        )
        assert response.status_code == 200, response.text
        token = active_client.cookies.get("__Host-sentinel_session")
        csrf = response.json()["csrf_token"]
        return {
            "client": active_client,
            "token": token,
            "csrf": csrf,
            "headers": {get_settings().csrf_header_name: csrf, "Origin": "https://testserver"},
            "response": response,
        }

    default_client = client
    return authenticate
