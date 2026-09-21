# Docker en Windows PowerShell — Sprint 1B.1 Closure Candidate

La guía operativa vigente es [SPRINT_1B_1_COMMANDS_POWERSHELL.md](SPRINT_1B_1_COMMANDS_POWERSHELL.md).
Este resumen utiliza el proyecto Compose aislado `sentinelai-closure`.

## Inicio local

Requiere Docker Desktop iniciado con contenedores Linux y PowerShell. La copia
de `.env` se crea solamente si todavía no existe.

```powershell
Set-Location "C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate"
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
.\scripts\docker-build.ps1
.\scripts\docker-up.ps1
.\scripts\docker-verify.ps1
```

Caddy publica la SPA con mocks en `http://127.0.0.1:8083` y enruta `/api` al
backend interno. Los endpoints `/api/v1/health`, `/api/v1/health/live` y
`/api/v1/health/ready` se comprueban por ese mismo puerto. PostgreSQL, Redis y
FastAPI no publican puertos adicionales en el host.

Las cookies de autenticación mantienen `Secure`, `HttpOnly`, `SameSite=Lax` y
prefijo `__Host-`; el acceso HTTP local al frontend no sustituye un entorno HTTPS
de autenticación. Las pruebas de contrato utilizan un cliente HTTPS.

## Operación y verificación

```powershell
Set-Location "C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate"
.\scripts\docker-build.ps1 -NoCache
.\scripts\docker-up.ps1 -Build
.\scripts\docker-logs.ps1 -Tail 100
.\scripts\docker-logs.ps1 -Service backend -Follow

# Todos los gates backend, PostgreSQL temporal, migraciones y secret scan.
.\scripts\backend-quality.ps1
if ($LASTEXITCODE -ne 0) { throw 'Backend quality no aprobado.' }

# Retira contenedores y redes del stack local; conserva sus volúmenes.
.\scripts\docker-down.ps1
```

`backend-quality.ps1` utiliza un proyecto de pruebas independiente con PostgreSQL
en `tmpfs`, ejecuta los controles y devuelve un código distinto de cero ante un
fallo. Sus pruebas no se ejecutan sobre la base persistente del stack local.

## Configuración y diagnóstico

`.env` es local y no se empaqueta. `FRONTEND_PORT=8083` enlaza solo loopback.
`CORS_ORIGINS`, `TRUSTED_ORIGINS` y `CSRF_HEADER_NAME` deben ser coherentes con el
cliente. Las credenciales de ejemplo son exclusivas del entorno local.

Si Docker no responde, comprueba el motor Linux de Docker Desktop. Si 8083 está
ocupado, ajusta el puerto y los orígenes correspondientes en `.env`. Si falla
readiness, consulta los logs de `migrations`, `backend` y `postgres` siguiendo la
guía vigente. Detener y volver a iniciar con los scripts conserva los datos.
