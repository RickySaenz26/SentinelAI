# Sprint 1B — Backend Foundation Report

> Historical evidence recorded on 2026-09-19. The limitations and paths in this
> report describe the original Sprint 1B execution and were not silently rewritten.
> Current closure evidence is in
> [WORK_HANDOFF_SPRINT_1B1_CLOSURE.md](../../WORK_HANDOFF_SPRINT_1B1_CLOSURE.md)
> and [SPRINT_1B1_REMEDIATION_REPORT.md](SPRINT_1B1_REMEDIATION_REPORT.md).

## Scope delivered

- PostgreSQL persistence with SQLAlchemy 2 and Alembic (`20260919_01`, `20260919_02`).
- Dedicated Compose migration service plus migration/runtime PostgreSQL roles.
- Managed identity foundation: Argon2id credentials, opaque hashed sessions, CSRF, rotation, logout, recovery contracts, and explicit hidden-input bootstrap.
- Organizations, memberships, seeded RBAC roles/permissions, tenant transaction context, forced RLS, optimistic concurrency, audit hash chain, and transactional outbox records.
- Required `/api/v1` contracts: health, session, recovery, me, organizations, memberships, roles, and security audit events.
- Frontend deliberately remains unchanged and continues to use mock data.

## Evidence recorded on 2026-09-19

| Control | Result |
| --- | --- |
| Alembic from empty ephemeral database | Passed through `20260919_02` |
| HTTP contract smoke | Passed: login, opaque cookie/CSRF, `/me`, RBAC, RLS isolation across two organizations, audit event, optimistic-concurrency conflict, recovery non-enumeration |
| Compose configuration and migrations | Passed |
| Docker health + Caddy public proxy | Passed at `127.0.0.1:8081` |
| Backend OpenAPI contract check | 15 paths; required paths present |
| Frontend lockfile install, lint, typecheck, build | Passed |
| Frontend production dependency audit | Passed: no known vulnerabilities |
| Local secret scan | Passed: no high-confidence findings |

## Quality-gate limitation

`ruff`, `pytest`/coverage, and `pip-audit` could not be invoked in the ephemeral backend validation container because its development dependencies were unavailable from the configured Python package registry during this run. The attempted install failed before executing those tools; this report does **not** claim their results or the 90% coverage target. The self-contained HTTP smoke test was executed with runtime dependencies and a disposable PostgreSQL database instead.

Re-run the remaining gates when Python package registry access is available:

```powershell
docker compose run --rm --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace --entrypoint sh backend -c "pip install -r requirements-dev.txt && ruff check app alembic && pytest --cov=app --cov-report=term-missing && pip-audit -r requirements.txt"
```

## Boundary and release posture

Sprint 1B is not production-ready. It has no recovery-email provider, distributed rate-limit store, secret manager, backup/restore exercise, production TLS termination, external security assessment, monitoring/SLO validation, assets, authorization workflows, scanners, jobs, findings, reports, or LLM integration. No public registration is included.
