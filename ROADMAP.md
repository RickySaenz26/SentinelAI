# Roadmap — estado de Sprint 1B.1

Repositorio canónico: `C:\SentinelAI\platform`.

Sprint 0 estabilizó el scaffold; Sprint 1A aportó contenedores; Sprint 1B introdujo
identidad, PostgreSQL y tenancy. Son antecedentes, no aceptación de seguridad.

Sprint 1B.1 completó la remediación verificable de la foundation: jerarquía de
membresías, defensa SQL de platform_admin, locking atómico, convergencia de
migraciones automatizada, auditoría secuencial, outbox, CSRF, gates reproducibles
y smoke autenticado del stack Compose desplegado. Decisión y evidencia:
`WORK_HANDOFF_SPRINT_1B1_CLOSURE.md`.

Elementos de cierre completados:

- [x] Ruff, pytest/cobertura de ramas, pip-audit y secret scan reproducibles.
- [x] Convergencia de migraciones limpias e históricas con grants comparados.
- [x] Smoke autenticado HTTPS efímero con flujos positivos y negativos.
- [x] Documentación canónica, handoff y mecanismo de paquete sanitizado.

El siguiente sprint requiere auditoría independiente, revisión de Work/fundador y
una decisión explícita.
No se implementó ni autorizó escaneo, activos, jobs, hallazgos, informes, IA,
correo externo, publicación o conexión del frontend a contratos reales.

Antes de beta/piloto/producción se necesitan TLS, gestión de secretos,
backups/restauración, MFA/SSO según producto, recovery delivery seguro,
rate limiting distribuido, observabilidad y revisión independiente.
Aceptar la foundation local no equivale a production-ready.

## Sprint 2A — incremento 1 (working tree para revisión)

El usuario autorizó política de laboratorio e inventario API-first, sin scanner.
Se implementan activos IPv4 exactos `unverified`, política vacía deny-by-default,
exclusiones, cuotas, aislamiento, archivo lógico e idempotencia HTTP de POST/DELETE.
Contrato, límites y operación: `docs/engineering/SPRINT_2A_INCREMENT_1.md`.
Resultados de ejecución: `docs/engineering/SPRINT_2A_INCREMENT_1_RESULTS.md`.

No es el cierre de todo Sprint 2A. Siguen pendientes evidencia cifrada, verificación
humana, autorizaciones y elegibilidad; tampoco hay jobs, scanners ni UI integrada.
Registrar un activo no acredita propiedad ni autoriza conectarse al objetivo.
