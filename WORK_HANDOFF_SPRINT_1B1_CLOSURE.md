# SentinelAI Security — Sprint 1B.1 Closure Handoff

## 1. Purpose and scope

This handoff records the verified closure of the Sprint 1B/1B.1 backend and
security foundation. It covers documentation, reproducible quality gates,
migration convergence and authenticated acceptance of the deployed local Compose
stack. It does not authorize external publication or production deployment.

Sprint 1B.1 closure does **not** mean the entire SentinelAI product is
production-ready.

## 2. Canonical repository and branch

- Repository: `C:\SentinelAI\platform`
- Branch: `remediation/sprint-1b1-closure-gates`
- Packaging authority: `scripts/package-closure.ps1`

## 3. Checkpoint lineage

| Stage | Commit |
| --- | --- |
| Recovery baseline | `40fad40cf90664a8744ce2036a522cf02e9e5b27` |
| Pass A — quality and migration gates | `e5e43695eb673d937803523c3aa414d5d7db4095` |
| Pass B — authenticated Compose smoke | `568e565260ad1672dd195d4174c56eb02f534797` |

Pass C documentation and packaging changes remain uncommitted for human review.

## 4. Implemented Sprint 1B/1B.1 capabilities

- FastAPI/Python 3.12 backend with PostgreSQL, SQLAlchemy 2 and Alembic.
- Managed identity bootstrap/onboarding; no public registration.
- Organizations, memberships, seeded roles and centralized hierarchical policy.
- Opaque hashed sessions, Argon2id credentials, recovery contracts and CSRF.
- Optimistic concurrency with atomic `If-Match` enforcement and last-owner guard.
- Sequential audit hash chain and transactional outbox with deterministic keys.
- Local Docker Compose topology with PostgreSQL, Redis, backend and mock-based
  React frontend behind Caddy.

The frontend remains mock-based. Product scanners, asynchronous product jobs,
findings, report generation and local LLM integration are not implemented here.

## 5. Security controls

- Deny-by-default RBAC and tenant-aware membership policy.
- Forced PostgreSQL RLS on tenant tables; runtime role is `NOSUPERUSER` and
  `NOBYPASSRLS` with restricted grants.
- `platform_admin` tenant-assignment defenses in policy and database constraints.
- `__Host-` session cookie with Secure, HttpOnly, SameSite=Lax, Path=/ and no
  Domain; opaque token material is stored only as hashes.
- Trusted-Origin and constant-time CSRF validation for authenticated mutations.
- Transactional audit/outbox integrity and audit immutability for runtime.
- Lock ordering and concurrency coverage for bootstrap, recovery, organization
  switching, memberships and audit sequencing.

## 6. Migration state

Current chain: `20260919_01` → `20260919_02` → `20260919_03` → `20260920_04`.
The automated PostgreSQL convergence test builds both a clean/current database
and a database from preserved historical 01/02 snapshots followed by current
03/04 migrations. It compares normalized schema objects and privileges/grants,
normalizing only nondeterministic `pg_dump` restrict nonces.

## 7. Pass A evidence

- Ruff format and lint: PASS.
- Automated migration convergence: PASS.
- Weak pytest exception assertion replaced with a meaningful match.
- Package authority guard accepts the canonical Git workspace while preserving
  branch, ancestry, file-authority and exclusion checks.
- 229 tests passed; statement coverage 99.47%; branch coverage 97.17%.
- pip-audit and secret scan: PASS.

## 8. Pass B authenticated Compose evidence

An isolated ephemeral Compose project brought up PostgreSQL, Redis, Alembic,
backend and frontend/Caddy with HTTPS using an ephemeral Caddy CA. It verified
successful login, cookie attributes, authenticated `/me`, CSRF-positive mutation,
tenant isolation, cross-tenant denial, viewer RBAC denial, runtime RLS/grants,
audit generation/chain/immutability, logout, session revocation and stale-session
rejection. Invalid credentials, unauthenticated access, missing/invalid CSRF and
untrusted Origin were also rejected.

Cleanup left zero ephemeral containers, networks and volumes and did not affect
normal SentinelAI volumes or create `.env` files.

## 9. Exact test and coverage results

- Collected: 229
- Passed: 229
- Failed: 0
- Skipped: 0
- Statement coverage: 99.47%
- Branch coverage: 97.17% (required minimum: 90%)

## 10. Dependency audit

`pip-audit` completed successfully with no known vulnerabilities in the pinned
backend requirements during Pass A.

## 11. Secret scan

The repository high-confidence secret scan passed during Pass A and again during
Pass C on 2026-09-23. Pass C also scans the extracted final package representation
before the artifact is accepted.

## 12. Authenticated smoke result

Pass B authenticated deployed Compose smoke: PASS. The full smoke was rerun during
Pass C on 2026-09-23 and passed again, including cleanup of every ephemeral
container, network and volume.

The temporary `platform_admin` fixture exists only inside the ephemeral acceptance
environment. It is destroyed with that environment, is not persisted operational
credential material and is not a production onboarding recommendation.

Pass C also reran Ruff format/lint, the 229-test backend quality gate, coverage,
pip-audit, migration convergence, source secret scan, frontend lint/typecheck/build
and Compose configuration validation successfully before packaging.

## 13. Known non-blocking limitations

- Local HTTP frontend operation is not a production TLS architecture.
- Secrets are local examples; a production secret manager is not implemented.
- Backup/restore validation, external audit anchoring and observability/SLO remain.
- Recovery has no real delivery provider; rate limiting is process-local.
- Outbox publisher/workers and distributed delivery are not implemented.
- Frontend screens remain mock-based and are not product-integrated with backend
  contracts.

## 14. Explicit Sprint 1B.1 exclusions

Production deployment approval, public registration, assets/scope authorization,
scanners, product jobs, findings, reporting, email integration, external
publication and LLM integration are outside this closure.

## 15. Operating instructions

From `C:\SentinelAI\platform`:

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
docker compose config --quiet
.\scripts\backend-quality.ps1
.\scripts\authenticated-compose-smoke.ps1
```

See [the operations guide](docs/engineering/SPRINT_1B_OPERATIONS.md) for the full
local lifecycle. Do not use the example credentials outside local development.

## 16. Final artifact information

- Intended artifact: `SentinelAI-Sprint-1B.1-Closure-Final-20260923.zip`
- Intended path: `C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Final-20260923.zip`
- Source: uncommitted Pass C working tree after all final gates pass.
- Historical Sprint ZIP files are not overwritten.

## 17. SHA-256 recording policy

The final SHA-256 is computed only after the ZIP is complete and inspected. It is
recorded externally in the Pass C execution response rather than embedded into
the already-built ZIP, avoiding a stale or circular self-reference.

## 18. Next-step recommendation

Perform a final independent audit of the uncommitted diff, gate logs, package
contents and externally recorded SHA-256. Only after explicit human approval
should a separate decision be made about commit/release handling or a future
sprint. Do not infer production readiness from this foundation closure.
