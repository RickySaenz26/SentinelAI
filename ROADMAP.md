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
