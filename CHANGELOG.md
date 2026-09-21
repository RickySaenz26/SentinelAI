# Changelog

Todos los cambios relevantes se documentarán aquí.

## [Unreleased]

### Sprint 1B.1 Closure Candidate — 2026-09-20

- Política central valida actor, acción, rol actual/propuesto, tenant e identidad.
- Defensa PostgreSQL de bootstrap único y platform_admin inmutable; runtime mínimo.
- If-Match atómico con locks y un solo ganador concurrente.
- Nueva revisión 20260920_04 sin reescribir 01–03; comparación histórica/limpia.
- Audit secuencial v3; hashes legacy v1/v2 preservados explícitamente.
- Outbox con claves deterministas y atomicidad/deduplicación; sin publisher.
- CSRF configurable coherente con CORS/OpenAPI/auth, e If-Match en preflight.
- Revalidación de login/expiración tras locks y serialización multi-organización.
- CLI allowlist cerrado y pruebas de bootstrap/onboarding/repetición/rollback.
- Smoke convertido a pytest; contratos reales, RLS, grants y concurrencia.
- Quality autocontenido no root con PostgreSQL tmpfs y CI/gate PowerShell obligatorios.
- Documentación y paquete de cierre; fuentes anteriores intactas.

### Added

- Foundation Sprint 1B: Alembic, PostgreSQL, identidad administrada, sesiones opacas, CSRF, recovery contracts, organizaciones, membresías, RBAC, RLS, audit log y outbox.
- Servicio de migración y rol runtime PostgreSQL separado para el entorno local Compose.
- Operaciones y bootstrap local documentados para Sprint 1B.

- Pruebas y cobertura del backend.
- Logging estructurado y correlación mediante `X-Request-ID`.
- Endpoints de liveness y readiness.
- CI, auditoría programada, Dependabot y control básico de secretos.
- Scripts y documentación PowerShell para Windows 11.
- Contenedores reproducibles para frontend y backend, con ejecución no root y healthchecks.
- Plataforma Docker Compose local con PostgreSQL y Redis aislados y persistencia explícita.
- Scripts PowerShell idempotentes para construir, iniciar, detener, inspeccionar y verificar Docker.
- Validación CI de Compose, imágenes y healthchecks de la plataforma local.

### Changed

- Dependencias directas del backend fijadas a versiones verificadas.
- Configuración CORS explícita y preparada para variables de entorno.
- Estado y comandos del proyecto documentados de forma reproducible.
- Configuración de producción rechaza orígenes CORS HTTP inseguros.

### Fixed

- Compilación TypeScript del componente `AppShell` con React 19.
