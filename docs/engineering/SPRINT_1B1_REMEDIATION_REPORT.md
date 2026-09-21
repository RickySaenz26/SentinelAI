# Sprint 1B.1 — Backend Foundation Remediation Report

## Scope and boundary

This is an isolated remediation of Sprint 1B. It does not make the product production-ready and it does not add public registration, scanners, jobs, findings, reports, LLMs, external delivery, or frontend-to-backend integration. The frontend remains mock-based.

The source package SHA-256 was verified before extraction:

`DE8CD17200DF10AEA114C9EBB757E895B9201376E9229F39FACA2A37E0AA2139`

## Security remediation delivered

| Area | Implemented control |
| --- | --- |
| Tenant role escalation | `platform_admin` is never tenant-assignable. `org_owner` may assign only `org_owner`, `security_manager`, `analyst`, `viewer`, and `auditor`; `security_manager` may assign only `analyst`, `viewer`, and `auditor`. The target role is resolved only from global roles or roles of the current organization. |
| Tenant boundary | Membership update/revoke verifies the path organization. RLS is forced and runtime has no direct role mutation privileges. |
| Sessions | Sessions bind to an active membership. Authentication rejects inactive/deleted user, organization, and membership; database triggers revoke active sessions when any of them becomes suspended or deleted. Sliding expiration is committed by the dependency lifecycle. |
| Concurrency | Last-owner protection and bootstrap use PostgreSQL transaction advisory locks. Recovery consumption uses `FOR UPDATE` and sets `consumed_at` in the same transaction. |
| Database integrity | Explicit Alembic DDL replaces metadata creation. It adds role-scope checks, global/tenant role uniqueness, membership-role tenant trigger, session-membership trigger, and outbox idempotency/pending indexes. |
| Audit and outbox | Audit canonical material includes event id, UTC timestamp, actor type, prior hash, resource and request data. Per-organization advisory locking serializes the chain. Events are constructed before insert so runtime keeps no audit update privilege. Outbox supports an idempotency key. |
| CSRF/CORS | The configured CSRF header is explicitly allowed by CORS. Mutations use constant-time CSRF hash comparison and trusted-origin validation. |
| Runtime discipline | SQLAlchemy engine/session factory is cached. Runtime and migration roles remain separated; quality tooling is a separate Docker target and is absent from runtime. |
| Managed onboarding | `python -m app.platform.create_managed_user` uses hidden password input, normalized email, bounded password validation, explicit organization UUID and a non-platform tenant role. It never emits credentials. |

## Executed evidence — 2026-09-19

| Gate | Result |
| --- | --- |
| Source archive SHA-256 | PASS |
| `docker compose config --quiet` | PASS |
| Empty PostgreSQL install: `20260919_01` → `02` → `03` | PASS |
| One-revision downgrade and re-upgrade in `sentinelai_s1b1_remediated_test` | PASS |
| HTTP contract smoke on isolated database | PASS: login, opaque cookie + CSRF, `/me`, roles, RLS organization isolation, audit, optimistic-concurrency conflict, recovery non-enumeration |
| Runtime RLS/grants probe | PASS: one organization visible with tenant context; `roles INSERT = false`; `security_audit_events UPDATE = false` |
| Ruff format and lint | PASS |
| `pytest` branch coverage | FAIL: 10 tests pass, total branch coverage is 48.37%, below the non-negotiable 90% threshold |
| `pip-audit` | NOT ACCEPTED: the quality container could not resolve `pypi.org`; no clean audit result is claimed |

## Acceptance decision

**NO ACEPTAR yet.** The core remediation and its migration/RLS/HTTP evidence are present, but the required coverage threshold and a clean Python dependency audit have not passed. Do not lower coverage, weaken RLS, merge migration/runtime roles, loosen CSRF, or grant audit update access to make the gates pass.

## Delivery artifact

The delivery ZIP excludes `.env`, dependency directories, coverage, caches and build output. Its hash is recorded externally at packaging time so the archive does not self-reference a stale value.

## Required next work

1. Add integration and concurrency tests until branch coverage is at least 90% without reducing scope.
2. Run `pip-audit` successfully in a network-enabled quality environment and remediate any findings.
3. Re-run the complete Compose smoke after rebuilding both images, then package only the clean workspace; exclude `.env`, caches, test databases, `node_modules`, and build artifacts.
