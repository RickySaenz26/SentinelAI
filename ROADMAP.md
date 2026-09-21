# Roadmap — estado de Sprint 1B.1

Workspace: `C:\SentinelAI\SentinelAI-Sprint-1B.1-Closure-Candidate`.

Sprint 0 estabilizó el scaffold; Sprint 1A aportó contenedores; Sprint 1B introdujo
identidad, PostgreSQL y tenancy. Son antecedentes, no aceptación de seguridad.

El trabajo actual sigue siendo Sprint 1B.1: jerarquía de membresías, defensa SQL de
platform_admin, locking atómico, convergencia, auditoría secuencial, outbox, CSRF y
gates reproducibles. Decisión y evidencia: `WORK_HANDOFF_SPRINT_1B1_CLOSURE.md`.

El siguiente sprint requiere revisión de Work/fundador y una decisión explícita.
No se implementó ni autorizó escaneo, activos, jobs, hallazgos, informes, IA,
correo externo, publicación o conexión del frontend a contratos reales.

Antes de beta/piloto/producción se necesitan TLS, gestión de secretos,
backups/restauración, MFA/SSO según producto, recovery delivery seguro,
rate limiting distribuido, observabilidad y revisión independiente.
Aceptar la foundation local no equivale a production-ready.
