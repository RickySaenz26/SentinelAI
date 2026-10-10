# Etapa 4B — contrato previo a implementación

Fecha: 2026-10-09, America/Lima. Base comprobada local y en origin:
`e45bd7dfe2d057d819a210de860712d291043727`, rama
`feature/sprint-2a-assets-scope`, árbol/índice limpios. Este documento se escribe
antes del código. Los resultados 4A son históricos; el plan general del incremento
no está versionado y no se le atribuyen requisitos. Autoridad: prompt 4B del usuario.

## Frontera

Juicio de control técnico atribuido a una cuenta, nunca propiedad legal ni permiso
de escaneo. `ownership_status=unverified`. Dos cuentas no prueban dos personas.
Sin scanner, destinos/SENATI, evaluate, UI, workers, dispatcher, paquete o cambios Git
publicados. Se reutilizan ADR-009, storage Linux, retención 90 días y autoridad 4A.

## RBAC cerrado

| Rol | Presentar | Leer solicitudes | Decidir | Retirar pendiente | Revocar aprobación |
| --- | --- | --- | --- | --- | --- |
| org_owner / security_manager | Solo evidencia propia | Tenant | Otro user_id | Solo propia | Tenant, incluso propia |
| analyst | Solo evidencia propia | Solo propias | No | Solo propia | No |
| auditor | No | Tenant | No | No | No |
| viewer / platform_admin | No | Solo resumen mínimo | No | No | No |

Permisos explícitos `control:submit/read/read_own/decide/withdraw/revoke/summary`.
El bypass genérico platform:admin no aplica. Revalidación de rol y permiso real en
cada fase; presenter=author de evidencia por user_id. Revisor distinto de ambos.
Decidir requiere además conservar `evidence:read`, también después del I/O y en SQL.

## Estados y compatibilidad

Solicitud inmutable + proyección mutable solo por triggers cerrados + eventos
append-only. pending → approved/rejected/withdrawn/invalidated; un terminal y una
pendiente por activo. Revocación, sustitución e invalidación posterior de una
aprobación añaden historia, sin reescribir su decisión. Expiración es calculada con
clock_timestamp(), no un worker. Vigencia hasta min(decisión+30 días, creación de
evidence_versions+90 días); nunca reiniciar retención.
Una pendiente cuya evidencia ya no se conserva no puede decidirse: al intentar
presentar evidencia nueva válida, el trigger cierra la pendiente vencida mediante
evento `invalidated/retention_expired` antes de insertar la nueva. Sin workers.

Snapshot completo de activo y versión, con compatibilidad explícita: mismo tenant,
UUID, tipo IPv4, canonical_target y ownership_status unverified, no archivado;
versión actual >= capturada. Solo nombre/criticidad pueden variar sin invalidar.
Cambiar versión no se ignora: create exige If-Match del activo; decisiones y demás
mutaciones exigen versión de proyección. Archivo/generación/admisión incompatible
cierran pendientes y desactivan aprobaciones en la misma transacción 4A mediante
triggers nuevos de 09; no se modifica 08 ni se da acceso directo al publicador.

Renovación usa `renews_review_id`, del mismo activo, apuntando a aprobación previa;
si ya existe una aprobación histórica, omitir ese enlace es un conflicto SQL.
requiere nueva lectura/decisión. Rechazo o retiro no toca la anterior. Una nueva
aprobación sustituye todas las anteriores aún no sustituidas; no reactivación.
Readmisión admite evidencia histórica propia íntegra/conservada/compatible, pero
nueva solicitud ligada a la generación vigente; nunca replay de autoridad antigua.

## Contrato API propuesto para congelar con implementación

POST/GET `/api/v1/assets/{asset_id}/control-reviews`; GET
`/api/v1/control-reviews/{review_id}`; POST sufijos `/decisions`, `/withdrawals`,
`/revocations`; GET `/api/v1/assets/{asset_id}/control-status` y `/control-summary`.
JSON estricto, máximo streaming 16384 bytes, sin compresión, duplicados ni extras.
Cookie existente, CSRF requerido para POST, Origin opcional pero validado si existe.
If-Match entero positivo (409 en conflicto), Idempotency-Key requerido en mutaciones.
Todas las respuestas y errores no-store. Paginación limit 1–100, cursor UUID acotado.

Alta: `{evidence_id, evidence_version, renews_review_id?}`. No tenant/autor/IP.
Decisión: `{decision: approved|rejected, checklist_version: 1, checklist:
{console_identity, inventory_match, administrative_control}, reason_code}`.
Cada punto: confirmed/not_confirmed/not_assessable. Aprobar exige los tres confirmed
y reason_code=control_confirmed. Rechazar: mismatch/incomplete/not_assessable.
Retiro: reason_code=presenter_withdrawal. Revocación: confidence_withdrawn/error_found.
Sin texto libre. Resumen: asset_id, review_count, ownership_status; sin identidades,
motivos, contenido ni valid=true. Status: valid, checked_at, reasons de catálogo y
referencia histórica autorizada; nunca inferir valid=true de una proyección guardada.

El revisor lee mediante el GET existente de contenido de evidencia. La decisión
queda ligada por FK al evento auditado de lectura de esa versión por ese user_id,
posterior a la creación de la solicitud. La aprobación descifra/verifica de nuevo,
sin convertir esa comprobación automática en la lectura humana previa requerida.

## SQL, transacciones y límites de confianza

09 añade requests/projections/events, RLS forzado, FK tenant compuestas, constraints
de terminal/pending/identidad/tiempo/checklist, grants por columnas y triggers
inmutables. Runtime no modifica proyecciones o historia. Contexto de sesión se fija
solo en el servidor tras get_actor; SQL revalida sesión, membership, rol y plazos.
Los GUC locales `app.control_session` y `app.control_user` se fijan desde
ActorContext; no se reutiliza `app.user_id`, que identidad vacía tras resolver tenant.
No se confunde un GUC de runtime con una credencial humana autónoma: runtime/DBA
son frontera confiable, como en identidad existente. SQL no puede demostrar lectura
de filesystem ni veracidad del juicio; la aplicación debe verificar storage real.

Orden: organización → activo → solicitud/proyección → auditoría. Primera transacción
captura identidad/referencias; se cierra antes de abrir storage/llaves/descifrar.
Segunda transacción reautentica, toma locks en orden, revalida identidad, activo,
generación, versiones y tiempo PostgreSQL. Ninguna espera humana/I/O bajo locks.
Triggers generan auditoría encadenada y outbox de cada transición junto a su estado;
replay de 24h se confirma en ese mismo commit y guarda solo IDs. Auditoría conserva
formato v3 y trigger de secuencia de 04; se prueba verify_chain con eventos mixtos.
Publicaciones 4A conservan íntegramente sus constraints de eventos y privilegios.

Commit incierto: error 503 sanitizado; repetir misma clave tras reautorizar devuelve
referencias históricas, no afirma vigencia actual. Downgrade 09 rechaza historia;
sin historia puede retirar sus objetos sin tocar datos de 4A.

## Aceptación pendiente (no resultados)

Regresiones SQL/HTTP con PostgreSQL y storage reales: referencias/actor ajenos,
autoaprobación entre sesiones/roles, lectura previa, checklist, tiempos, renovación,
sustitución, revocación, rollback/replay y carreras de sesión/permisos/política/activo
durante I/O y dos revisores. Migraciones fresh/08 poblada/convergencia/downgrade;
gate/Ruff/ramas>=90%, storage sin red, OpenAPI, pip check/audit, scanner, exclusiones,
ambos HTTPS, hashes y cleanup. Guía reproducible de dos personas por separado de
fixtures automatizados. Resultados finales y pendientes se registrarán en handoff.
