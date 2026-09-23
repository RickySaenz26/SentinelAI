# Handoff para ChatGPT Work — SentinelAI Sprint 1B

> Evidencia histórica de la entrega Sprint 1B original. Sus rutas, limitaciones y
> resultados describen la ejecución del 2026-09-19 y no representan el cierre
> vigente. Para el estado actual consulte
> [WORK_HANDOFF_SPRINT_1B1_CLOSURE.md](WORK_HANDOFF_SPRINT_1B1_CLOSURE.md).

## Contexto y autoridad

Codex implementó el Sprint 1B en una copia aislada. El prompt maestro y la baseline de diseño se preservaron como autoridad funcional. No se modificó la copia fuente de Sprint 1A.

## Entregables

| Elemento | Ruta |
| --- | --- |
| Workspace Sprint 1B | `C:\SentinelAI\SentinelAI-Sprint-1B-Backend-Foundation` |
| ZIP final | `C:\SentinelAI\SentinelAI-Sprint-1B-Backend-Foundation.zip` |
| SHA-256 | `DE8CD17200DF10AEA114C9EBB757E895B9201376E9229F39FACA2A37E0AA2139` |
| Informe técnico | `docs\engineering\SPRINT_1B_REPORT.md` |
| Operación y bootstrap | `docs\engineering\SPRINT_1B_OPERATIONS.md` |

## Implementado

- PostgreSQL 17, SQLAlchemy 2, Alembic y migraciones `20260919_01` y `20260919_02`.
- Rol de base separado para migración (`sentinelai_migrator`) y runtime (`sentinelai_runtime`), creado por `docker/postgres-init/10-create-runtime-role.sh`.
- Modelos iniciales: users, credentials, organizations, roles, permissions, role_permissions, memberships, sessions, recovery tokens, audit events y outbox.
- Roles seed: `platform_admin`, `org_owner`, `security_manager`, `analyst`, `viewer`, `auditor`.
- Sesiones opacas: token y CSRF guardados como hash; cookie `__Host-sentinel_session`, Secure, HttpOnly, SameSite=Lax; rotación y revocación.
- Identidad administrada: no existe registro público. Bootstrap explícito por `python -m app.platform.bootstrap` con contraseña por entrada oculta.
- Account recovery persistido; respuesta pública siempre `202`; no existe proveedor de email en Sprint 1B; adaptador de captura solo en modo test.
- Endpoints: health/live/ready, session/login/logout/rotate, account recovery, me, organización activa, organizations, memberships, roles y security audit events.
- RBAC deny-by-default, control de versión con `If-Match`, protección del último owner, revocación de sesiones al suspender/revocar membresía y rate limits locales de proceso.
- RLS FORCED en organizations, roles, memberships, security_audit_events y outbox_events. La fase previa de login puede consultar solo la propia membresía para resolver el tenant; después se limpia ese contexto y se aplica el tenant activo.
- Audit hash chain y outbox se escriben en la misma transacción que las mutaciones soportadas.
- Frontend se preservó sin conectar a estos contratos; conserva mocks por alcance.

## Verificaciones realizadas

- Migración Alembic desde una base PostgreSQL efímera vacía: correcta.
- Smoke HTTP contra base efímera: correcto para login, cookie/CSRF, `/me`, roles, RLS entre dos organizaciones, audit event, conflicto `If-Match` y recuperación sin enumeración.
- `docker compose config --quiet`: correcto.
- Stack completo validado: PostgreSQL, Redis, migraciones, backend y frontend saludables.
- Caddy y readiness: correcto en `http://127.0.0.1:8081`.
- OpenAPI: 15 rutas; rutas Sprint 1B requeridas presentes.
- Frontend: `pnpm install --frozen-lockfile`, lint, typecheck, build y `pnpm audit --prod --audit-level=high`: correctos; sin vulnerabilidades conocidas.
- Secret scan local: correcto; sin hallazgos de alta confianza.

## Pendiente / no afirmar como aprobado

No declarar Sprint 1B listo para producción ni cerrar todos los quality gates. Durante esta ejecución no fue posible instalar dependencias Python de desarrollo en el contenedor efímero por indisponibilidad del registro Python; por eso **no se ejecutaron** Ruff, pytest/cobertura ni pip-audit. No se debe inventar que se alcanzó cobertura 90%.

Cuando el registro Python esté disponible, ejecutar:

```powershell
Set-Location "C:\SentinelAI\SentinelAI-Sprint-1B-Backend-Foundation"
docker compose run --rm --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace --entrypoint sh backend -c "pip install -r requirements-dev.txt && ruff check app alembic && pytest --cov=app --cov-report=term-missing && pip-audit -r requirements.txt"
```

Tampoco están dentro de Sprint 1B: proveedor real de recovery email, rate limiting distribuido, secret manager, backup/restore probado, TLS productivo, observabilidad/SLO, assets, autorizaciones de alcance, scans, jobs, hallazgos, reportes o LLM.

## Recomendación para el siguiente paso

1. Work debe revisar el workspace y el informe técnico, verificando especialmente RLS, bootstrap, sesión/CSRF y los flujos de membresías.
2. Completar los tres quality gates Python pendientes sin debilitar controles ni bajar umbrales.
3. Si pasan, crear un cierre formal de Sprint 1B con resultados reproducibles.
4. Solo después iniciar Sprint 2 sobre una nueva copia aislada: activos y pruebas de control/propiedad; no introducir escaneo activo ni integraciones externas sin autorización de alcance.

## Instrucciones de colaboración

- No modificar `C:\SentinelAI\SentinelAI-Sprint-1A-Container-Platform` ni su ZIP.
- Trabajar únicamente sobre `C:\SentinelAI\SentinelAI-Sprint-1B-Backend-Foundation` o una copia derivada.
- Mantener `.env` fuera de ZIP, commits y reportes; el stack local actual usa puerto `8081` para coexistir con Sprint 1A.
- Preservar el frontend con mocks hasta una decisión explícita de integración.
- No eliminar RLS, CSRF, auditoría, hash chain, controles de concurrencia ni roles DB separados para hacer pasar pruebas.
