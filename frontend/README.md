# SentinelAI Security — frontend con mocks

SPA de React, TypeScript y Vite conservada durante Sprint 1B.1. Las pantallas
siguen utilizando datos simulados; este cierre se centra en la foundation del
backend y no integra scanners, trabajos, IA ni nuevas funciones del frontend.

## Verificación local

```powershell
Set-Location "C:\SentinelAI\platform\frontend"
pnpm install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw 'Instalación fallida.' }
pnpm lint
if ($LASTEXITCODE -ne 0) { throw 'Lint fallido.' }
pnpm typecheck
if ($LASTEXITCODE -ne 0) { throw 'Typecheck fallido.' }
pnpm build
if ($LASTEXITCODE -ne 0) { throw 'Build fallido.' }
pnpm audit --prod --audit-level=high
if ($LASTEXITCODE -ne 0) { throw 'Auditoría fallida.' }
```

La imagen frontend se construye con Node 24.13.1, pnpm 12.4.1 y el lockfile
congelado. Caddy sirve la compilación en `http://127.0.0.1:8083`, enruta `/api` al
backend y mantiene fallback a `index.html` para rutas de cliente.

Para levantar el stack o ejecutar gates backend desde la raíz:

```powershell
Set-Location "C:\SentinelAI\platform"
.\scripts\docker-up.ps1 -Build
.\scripts\docker-verify.ps1
.\scripts\backend-quality.ps1
if ($LASTEXITCODE -ne 0) { throw 'Backend quality no aprobado.' }
```

La [guía PowerShell vigente](../docs/engineering/SPRINT_1B_OPERATIONS.md)
incluye configuración inicial de `.env`, migraciones y operación. Los gates
backend usan PostgreSQL efímero; las cookies de sesión conservan sus flags de
seguridad y las pruebas de autenticación usan un cliente HTTPS.
