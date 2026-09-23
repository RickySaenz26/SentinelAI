# Comandos heredados de Sprint 0 — referencia reemplazada

La operación actual corresponde a Sprint 1B.1 Closure Candidate. Los comandos de
Sprint 0 dependían de un backend sin PostgreSQL y ya no representan la suite
vigente. La guía completa es
[SPRINT_1B_OPERATIONS.md](SPRINT_1B_OPERATIONS.md).

```powershell
Set-Location "C:\SentinelAI\platform"
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
.\scripts\docker-up.ps1 -Build
.\scripts\docker-verify.ps1
.\scripts\backend-quality.ps1
if ($LASTEXITCODE -ne 0) { throw 'Backend quality no aprobado.' }
```

El frontend local se publica en `http://127.0.0.1:8083`. La verificación backend
crea PostgreSQL efímero y ejecuta migraciones, pruebas y controles de seguridad.
La suite requiere ese entorno; no debe ejecutarse sobre la base persistente
del usuario.

Para verificar el frontend por separado:

```powershell
Set-Location "C:\SentinelAI\platform\frontend"
pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw 'Instalación frontend fallida.' }
pnpm lint
if ($LASTEXITCODE -ne 0) { throw 'Lint frontend fallido.' }
pnpm typecheck
if ($LASTEXITCODE -ne 0) { throw 'Typecheck frontend fallido.' }
pnpm build
if ($LASTEXITCODE -ne 0) { throw 'Build frontend fallido.' }
pnpm audit --prod --audit-level=high
if ($LASTEXITCODE -ne 0) { throw 'Auditoría frontend fallida.' }
```

Los resultados históricos se conservan en `SPRINT_0_REPORT.md`; no constituyen
aprobación del candidato de cierre actual.
