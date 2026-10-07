# Etapa 3 — contrato de presentación y lectura autorizada

Contrato definido antes de implementar, 2026-10-06 (America/Lima).
Checkpoint: 320e66dea0276df6330a77556bdc2e2088976ea1, rama
feature/sprint-2a-assets-scope; árbol e índice limpios y origin local coincidente.
Especialización de ADR_009_LAB_STORAGE.md y del protocolo de etapas 1–2.
El plan general no está versionado en este checkout; el encargo de etapa 3 y los
handoffs delimitan esta entrega. No se inventan expedientes de verificación.

## Recursos y respuestas

Prefijo /api/v1/evidence/assets/{asset_id}. evidence_id es el UUID de una operación
committed con referencia inmutable en evidence_versions; no es object_id ni una
autorización. El servidor asigna version consecutiva por activo bajo coordinación.

| Método y sufijo | Respuesta |
| --- | --- |
| POST (sin sufijo) | 201, metadatos actuales del recurso; Idempotency-Replayed |
| GET /summary | 200, asset_id, evidence_count, ownership_status=unverified, request_id |
| GET (sin sufijo) | 200, items de metadatos, next_version para paginar, request_id |
| GET /{evidence_id} | 200, metadatos autorizados |
| GET /{evidence_id}/content | 200, documento lab_control_attestation_v1 |

Metadatos: id, asset_id, version, submitted_at, retention_until, content_available
y request_id. No actor identificable, IP alternativa, contenido, rutas, object_id,
policy_hash, llaves, envelope, digest, nonce ni wrapper. Paginación por version
ascendente: after_version >=0, limit 1–100 (50 por defecto). Solo committed.
content_available expresa vigencia del plazo de conservación, no una comprobación
de salud del storage; una lectura todavía puede devolver un error sanitizado 503.

Cookie __Host-sentinel_session obligatoria en las cinco operaciones. POST exige
X-CSRF-Token (nombre configurable), Origin confiable cuando se envía, If-Match
entero positivo de versión del activo e Idempotency-Key de 1–128 caracteres
[A-Za-z0-9][A-Za-z0-9:._-]*. Se conserva 409 para versión obsoleta.
OpenAPI declara Origin opcional en POST (se valida si se proporciona),
Idempotency-Replayed de tipo string con valores true/false exclusivamente en 201
del POST, y Cache-Control: no-store en respuestas de éxito y errores declarados.
Estas declaraciones no modifican autenticación, CSRF ni validación de Origin.

Body POST: exclusivamente application/json, opcional charset=utf-8, sin compresión,
campos extra, duplicados, IP, adjuntos, URLs ni comandos. Máximo 16.384 bytes,
contados durante request.stream, incluso sin Content-Length o por fragmentos;
no se llama request.body/json ni se usa un modelo Body que precargue el documento.
Fechas nuevas: observación en las últimas 24 horas. El replay valida el esquema y
fingerprint sin reaplicar frescura a un documento ya registrado.

Errores sanitizados: 401 sesión inválida; 403 permiso/CSRF/Origin/política;
404 recurso ajeno/inexistente; 409 versión, cuota, replay incompatible, operación
pendiente o coordinación ocupada; 410 contenido fuera de retención; 413 body grande;
415 tipo/compresión; 422 entrada/cabeceras/documento inválidos; 503 storage/DB/audit
no disponibles o resultado de commit incierto. Todas las respuestas de estas rutas,
incluidos errores, usan Cache-Control: no-store. No se registran excepciones con
parámetros SQL ni cuerpo dentro de esta frontera sensible.

## Permisos y revalidación

| Rol | Presentar | Metadatos/contenido | Resumen mínimo |
| --- | --- | --- | --- |
| org_owner, security_manager | Sí | Todo su tenant | Sí |
| analyst | Sí | Solo presentación propia | Sí |
| auditor | No | Todo su tenant | Sí |
| viewer, platform_admin | No | No | Sí, tenant activo |

analyst mantiene el filtro de autor incluso con permisos adicionales de lectura.
viewer permanece limitado al resumen y auditor no puede presentar, aun si se le
asigna por error evidence:write. Además del rol, cada operación exige su permiso.

Permisos explícitos evidence:write, evidence:metadata, evidence:read,
evidence:read_own y evidence:summary. platform_admin/platform:admin se rechaza
explícitamente para operaciones sensibles incluso si recibe un permiso adicional.
La revisión 07 solo asigna estos permisos; conserva tablas, FK, RLS, grants y
migraciones 01–06. No hay entidades ni estados de aprobación.

Cada fase obtiene ActorContext desde cookie y sesión actuales. Se compara la
identidad completa (usuario/tenant/membresía/sesión) tras I/O y se comprueban permisos
actuales. El streaming de entrada, configuración, cifrado y filesystem ocurren
fuera de transacciones DB. La lectura autoriza primero, descifra fuera de DB,
revalida y confirma evidence.content_read antes de devolver contenido. Fallo o
incertidumbre de audit/commit impide entregar plaintext; un GET repetido es un nuevo
acceso auditado, no replay de una mutación. La auditoría de lectura no necesita outbox.

## Replay y recuperación

Se reutiliza http_idempotency_records: contexto actor/tenant/POST/ruta/clave y
fingerprint canónico del documento + If-Match. Durante reserva se almacena solo un
puntero operation_id en la misma transacción; NO una respuesta sensible histórica.
Plazo 24 horas desde reserva. Cada generación usa una clave interna distinta.
Replay pendiente/reclaimed/aborted falla cerrado sin repetir I/O; reconciliación
sigue siendo explícita. Tras vencer 24h puede reservarse otra generación cuando
no exista una reserva activa. Las operaciones y referencias conservan su historia.
Replay committed vuelve a autorizar activo activo/política/If-Match y devuelve
metadatos actuales. Confirmación mantiene referencia/cuota/auditoría/outbox atómicos.
Una respuesta de commit perdida conserva el objeto; el replay determina el estado
persistido sin borrar ni duplicar. No hay transacción atómica entre PostgreSQL y FS.

## Historia y límites

Retirar la IP o archivar el activo impide nuevas presentaciones y replay de POST;
no impide metadatos ni lectura histórica autorizada. Conservación de laboratorio:
90 días desde creación de la referencia confirmada; después, contenido devuelve
410 tras autorización. Metadatos/resumen siguen disponibles. No hay purga automática.
ownership_status permanece unverified. No se habilitan revisión humana, aprobación,
rechazo, revocación, control efectivo, scope/evaluate, scanner, UI, workers, publisher,
backups ni rotación operacional. Linux local soportado; Windows nativo falla cerrado.
Fixtures tmpfs prueban fallos de proceso, no durabilidad ante pérdida de energía.

Compose pasa las tres variables explícitas de storage; no crea llaves ni monta
automáticamente almacenamiento operativo. El operador todavía debe aprovisionar
roots privados en un filesystem Linux compatible y un montaje persistente revisado.
El smoke -Evidence crea exclusivamente roots y KEK dentro de /tmp del contenedor
efímero; no es aprovisionamiento productivo ni evidencia de durabilidad física.

La API identifica explícitamente la raíz protegida a partir de su archivo fuente,
sin depender del cwd ni de .git. El checkout completo exige la disposición
backend/app/api/v1/evidence.py y los marcadores compose.yaml, frontend/package.json,
scripts/backend-quality.ps1 y backend/alembic.ini; protege toda la raíz platform,
incluidos los hermanos de backend. Las imágenes Docker crean como root el marcador
/etc/sentinelai-application-root: /workspace para quality y /app para runtime.
Ese marcador debe coincidir exactamente con la raíz de aplicación identificada y
esta debe contener alembic.ini. Una disposición desconocida falla cerrada.
La configuración valida ambas ubicaciones antes de abrir llaves o storage;
la apertura sigue usando descriptores y nofollow, sin permitir //, traversal,
symlinks ni Windows nativo. No se reemplaza esta defensa por resolve().
