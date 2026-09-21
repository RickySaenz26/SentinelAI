# SentinelAI Security — Sprint 1B.1 Closure Candidate

Foundation local de backend: FastAPI/Python 3.12, PostgreSQL, SQLAlchemy 2 y Alembic;
monolito modular con `/api/v1`. El frontend React sigue usando mocks. No es una
entrega de producción ni incorpora scanners, activos, jobs, hallazgos, IA o integraciones.

Workspace: `C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate`.
Las entregas anteriores y sus ZIP no se modifican.

## Inicio local

```powershell
Set-Location "C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate"
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
docker compose config --quiet
docker compose build backend frontend
docker compose up --detach
.\scripts\docker-verify.ps1 -TimeoutSeconds 120
```

Docker Desktop debe estar iniciado. La SPA y proxy Caddy escuchan exclusivamente
en `127.0.0.1:8083`. PostgreSQL, Redis y backend no publican puertos al host. Las
credenciales de ejemplo son marcadores locales, no aptos para un despliegue real.
Las cookies requieren HTTPS: no se debilitan para la SPA HTTP local. Las pruebas
HTTP usan `https://testserver` y conservan CSRF/RLS reales.

## Quality backend completo

```powershell
.\scripts\backend-quality.ps1
```

Construye quality no root, crea PostgreSQL tmpfs en un proyecto aleatorio aislado,
aplica migraciones y ejecuta Ruff, pytest, cobertura de ramas ≥90%, pip-audit y
secret scan. Incluye contratos, RLS, grants, concurrencia y comparación de esquemas.
Devuelve código no cero ante cualquier fallo y retira solo sus contenedores/red
efímeros. No usa ni elimina volúmenes persistentes. Runtime no contiene tests ni
pytest/Ruff/pip-audit.

## Controles y límites

- Identidad administrada; bootstrap inicial y onboarding con contraseña oculta.
- Sesiones opacas como hash; cookies __Host-, Secure, HttpOnly, SameSite=Lax,
  Path=/, sin Domain. Argon2id y CSRF configurable.
- RLS forzado y roles PostgreSQL migrator/runtime separados.
- Política jerárquica central, platform_admin inmutable y defensa SQL.
- If-Match atómico, último owner protegido, audit secuencial append-only y outbox transaccional.
- Sin delivery recovery real, publisher outbox ni workers. Rate limiting de un proceso.

## Evidencia y colaboración

Consulta [el handoff de cierre](WORK_HANDOFF_SPRINT_1B1_CLOSURE.md),
[la verificación](docs/engineering/SPRINT_1B_1_SECURITY_VERIFICATION.md),
[PowerShell](docs/engineering/SPRINT_1B_1_COMMANDS_POWERSHELL.md) y
[las migraciones](docs/engineering/SPRINT_1B_1_MIGRATION_CONVERGENCE.md).
Solo los informes de cierre describen el estado actual. Los informes Sprint 0,
1A, 1B y el DOCX Remediation anterior son antecedentes, no evidencia de aprobación.

Para detener sin borrar datos: `docker compose down`.
No almacenes .env, tokens ni credenciales en ZIP o Git. Véase [SECURITY.md](SECURITY.md).
La licencia definitiva está pendiente: [LICENSE](LICENSE).
