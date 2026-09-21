# Arquitectura de contenedores — Sprint 1B.1 Closure Candidate

Workspace operativo: `C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate`.
Los comandos vigentes están en
[SPRINT_1B_1_COMMANDS_POWERSHELL.md](SPRINT_1B_1_COMMANDS_POWERSHELL.md).

## Alcance

La foundation local conecta FastAPI con PostgreSQL mediante SQLAlchemy y
Alembic. Incluye identidad administrada, sesiones opacas, CSRF, organizaciones,
membresías, RBAC, RLS forzado, auditoría y outbox transaccional. La SPA conserva
sus mocks. Redis está disponible como infraestructura, sin publisher ni workers
implementados en este sprint.

## Topología

```text
Navegador 127.0.0.1:8083 -> frontend/Caddy:8080 -> /api -> backend/FastAPI:8000
                               |                            |
                         edge + service                 service interna
                                                            |
                                                    PostgreSQL + Redis
                                                            ^
                                              migrations (Alembic; exit 0)
```

El proyecto Compose se llama `sentinelai-closure`. La red
`sentinelai-closure-edge` conecta el frontend; `sentinelai-closure-service` es
interna y conecta los servicios. Solo Caddy publica un puerto en loopback.
Los volúmenes `sentinelai-closure-postgres-data` y
`sentinelai-closure-redis-data` conservan datos después de `docker compose down`.

PostgreSQL debe estar saludable antes de las migraciones; el backend espera su
finalización correcta y Redis saludable. El frontend espera el healthcheck del
backend. Caddy enruta `/api` y utiliza `index.html` como fallback de rutas SPA.

## Imágenes y separación de responsabilidades

| Componente | Base fijada | Uso |
| --- | --- | --- |
| Backend | `python:3.12.12-slim-bookworm` | Runtime Python 3.12, FastAPI y Alembic. |
| Construcción frontend | `node:24.13.1-alpine` | pnpm 12.4.1 y lockfile congelado. |
| Servidor SPA | `caddy:2.11.4-alpine` | Archivos estáticos y proxy interno. |
| PostgreSQL | `postgres:17.6-alpine` | Persistencia, RLS, constraints, triggers y locks. |
| Redis | `redis:8.4.6-alpine` | Infraestructura local, sin workers de producto. |

La imagen backend runtime incluye dependencias de aplicación y migraciones.
Las herramientas Ruff, pytest y pip-audit y el código de pruebas pertenecen al
target separado `quality`; no se copian al runtime. Migrator y runtime utilizan
roles PostgreSQL distintos; runtime no posee tablas ni `BYPASSRLS`.

## Quality aislado

`compose.quality.yaml` inicia PostgreSQL temporal en `tmpfs`, sin puertos de host
ni volúmenes persistentes. Su contenedor `quality` ejecuta como usuario no root
y contiene código, pruebas, migraciones y herramientas. La red de pruebas
permite consultar registros de dependencias para pip-audit. Cada ejecución de
`.\scripts\backend-quality.ps1` asigna un proyecto independiente y lo retira al
terminar; un fallo del gate produce un código de salida no cero.

## Controles y límites

Backend y Caddy ejecutan como usuarios no root, con `no-new-privileges`,
`cap_drop: ALL`, filesystem de solo lectura y directorios temporales acotados.
Compose define límites de recursos, healthchecks y reinicio controlado.
PostgreSQL y Redis conservan las capabilities necesarias para sus entrypoints
oficiales y no exponen puertos al host.

Este stack HTTP local sirve la SPA con mocks y healthchecks; no constituye un
despliegue production-ready. Las cookies de sesión siguen requiriendo `Secure`
y las pruebas autenticadas usan HTTPS. TLS de borde, entrega real de recovery,
gestión de secretos, backups operativos y publicación del outbox quedan fuera
del cierre actual. Los resultados de validación pertenecen al informe de cierre,
no se deducen de esta descripción arquitectónica.
