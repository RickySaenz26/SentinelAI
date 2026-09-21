# Sprint 1A — infraestructura local reproducible y endurecimiento de contenedores

Fecha: 2026-09-19

## Objetivo cumplido

Se incorporó una plataforma local con contenedores para frontend, backend,
PostgreSQL y Redis, sin modificar el alcance funcional de Sprint 0. Los tres
endpoints de salud existentes se conservan: `/api/v1/health`,
`/api/v1/health/live` y `/api/v1/health/ready`.

## Cambios

- Dockerfile multietapa de backend Python 3.12, dependencias aisladas, runtime
  mínimo, usuario no root y healthcheck de readiness.
- Dockerfile multietapa de frontend que instala con `pnpm-lock.yaml`, ejecuta
  lint/build y sirve `dist` por Caddy con fallback SPA y cabeceras de seguridad.
- `compose.yaml` con red de borde y red interna, healthchecks, límites, volúmenes
  persistentes, restart controlado y PostgreSQL/Redis sin exposición al host.
- Ejemplos `.env`, guardas para CORS de producción, `.dockerignore` y scripts
  PowerShell seguros para Windows 11.
- Job CI de configuración, construcción, arranque y verificación HTTP; no
  publica imágenes ni usa permisos/tokens adicionales.
- Corregidos durante la validación local: manifiesto de desarrollo excluido del
  contexto del backend, usuario dedicado de Caddy, eliminación de su capability
  de archivo incompatible con `no-new-privileges` y exposición del backend solo
  mediante el proxy interno `/api`.

## Fuera del alcance deliberadamente

No se implementó autenticación, usuarios, RBAC, organizaciones, multitenancy,
modelos, conexiones a PostgreSQL/Redis, CRUD, Alembic, repositorios, Nmap,
Searchsploit, exploits, Ollama, llama.cpp, servicios externos ni sustitución de
datos simulados.

## Validación de esta entrega

| Control | Resultado real |
| --- | --- |
| `pnpm install --frozen-lockfile` | Aprobado; lockfile sin cambios. |
| `pnpm lint` | Aprobado. |
| `pnpm typecheck` | Aprobado. |
| `pnpm build` | Aprobado. |
| `pnpm audit --prod --audit-level=high` | Aprobado. |
| `python scripts/secret_scan.py .` | Aprobado; sin patrones de alta confianza. |
| `docker compose --env-file .env.example config --quiet` | Aprobado. |
| Exclusión del ZIP | Aprobado; sin `.env`, `node_modules`, `.venv`, cachés ni `dist`. |
| Construcción Docker de backend | Aprobada con Python 3.12.12. |
| Construcción Docker de frontend | Aprobada; `pnpm install --frozen-lockfile`, lint y build dentro de la imagen. |
| `docker compose up` + `docker-verify.ps1` | Aprobado; PostgreSQL, Redis, backend y frontend saludables. |
| Proxy Caddy `/api` y fallback SPA | Aprobados en `127.0.0.1:8080`. |
| Ruff format + lint | Aprobados en contenedor efímero Python 3.12.12. |
| pytest + cobertura | 7 pruebas aprobadas; cobertura total 92,80 %. |
| `pip-audit -r requirements.txt` | Aprobado; sin vulnerabilidades conocidas. |

Python 3.12 no está instalado en Windows, pero los controles backend se
ejecutaron en un contenedor efímero `python:3.12.12-slim-bookworm` con el
repositorio montado solo lectura. Las pruebas emitieron dos advertencias de
deprecación de Starlette/AnyIO, sin afectar el resultado.

La aceptación queda condicionada a ejecutar `.\scripts\verify.ps1` y
`.\scripts\docker-build.ps1`, `docker-up.ps1` y `docker-verify.ps1` con Docker
Desktop/Python 3.12 disponibles, y a que todos finalicen correctamente.
