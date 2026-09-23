# Sprint 1B.1 — Remediation and Closure Evidence

This report preserves the failed intermediate evidence from 2026-09-19 and then
records the later Pass A/Pass B closure evidence. The earlier failure is part of
the remediation history; it is not the current closure result.

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

## Historical executed evidence — 2026-09-19

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

## Historical acceptance decision

**NO ACEPTAR yet.** The core remediation and its migration/RLS/HTTP evidence are present, but the required coverage threshold and a clean Python dependency audit have not passed. Do not lower coverage, weaken RLS, merge migration/runtime roles, loosen CSRF, or grant audit update access to make the gates pass.

## Delivery artifact

The delivery ZIP excludes `.env`, dependency directories, coverage, caches and build output. Its hash is recorded externally at packaging time so the archive does not self-reference a stale value.

## Historical required next work

1. Add integration and concurrency tests until branch coverage is at least 90% without reducing scope.
2. Run `pip-audit` successfully in a network-enabled quality environment and remediate any findings.
3. Re-run the complete Compose smoke after rebuilding both images, then package only the clean workspace; exclude `.env`, caches, test databases, `node_modules`, and build artifacts.

## Current closure evidence — Pass A and Pass B

The historical blockers above were remediated without lowering coverage or
weakening RLS, RBAC, authentication, sessions, CSRF, tenant isolation, database
privileges, audit integrity or outbox transactionality.

| Gate | Current verified result |
| --- | --- |
| Ruff format | PASS |
| Ruff lint | PASS |
| Pytest | PASS: 229 collected, 229 passed, 0 failed, 0 skipped |
| Statement coverage | PASS: 99.47% |
| Branch coverage | PASS: 97.17% (required minimum 90%) |
| `pip-audit` | PASS: no known vulnerabilities |
| Secret scan | PASS: no high-confidence patterns |
| Migration convergence | PASS: clean/current and preserved historical chains converge in PostgreSQL, including grants |
| Authenticated deployed Compose smoke | PASS: HTTPS positive and negative authentication, CSRF, RBAC, RLS, audit and logout flows |
| Ephemeral smoke cleanup | PASS: 0 containers, 0 networks, 0 volumes and no persisted temporary credentials |
| Security regression assessment | PASS: no known critical security regression found |

Pass A is checkpoint `e5e43695eb673d937803523c3aa414d5d7db4095` and Pass B is
checkpoint `568e565260ad1672dd195d4174c56eb02f534797` on
`remediation/sprint-1b1-closure-gates`.

The temporary `platform_admin` used by the Pass B acceptance smoke exists only in
its ephemeral Compose environment, is destroyed during cleanup and is not a
normal production onboarding recommendation or persisted credential.

## Current closure disposition

The former 10-test/48.37%-branch and unavailable-audit state is superseded by the
verified evidence above. Sprint 1B.1 has a verified backend/security foundation
ready for final independent audit. This does **not** state that SentinelAI as a
whole is production-ready, and it does not add product scanners, jobs, findings,
reports, LLM integration or frontend/backend product integration.

## Pass C final revalidation — 2026-09-23

Before packaging, Pass C executed the complete backend quality gate again: 229
tests passed with statement coverage 99.47% and branch coverage 97.17%; Ruff,
pip-audit and the repository secret scan passed. The migration-convergence test
also passed in an isolated PostgreSQL execution after its normal Alembic
precondition. Frontend lint, explicit typecheck and production build passed in the
pinned build image; Compose configuration validation passed. The authenticated
deployed HTTPS smoke passed again and removed all of its ephemeral resources.
