# Sprint 2A — incremento 1: política local e inventario

Estado: implementación para revisión humana en `feature/sprint-2a-assets-scope`,
desde `55022c4eff2bc527c834edd0c43b130ecd60ddd0`. No es cierre de todo Sprint 2A,
aprobación para producción ni autorización para redes reales de SENATI.

## Alcance y límites de confianza

API-first: registrar, leer, modificar metadatos y archivar activos IPv4 exactos.
Todos conservan `ownership_status=unverified`, incluso después de editar.
No hay comprobación de propiedad, evidencia, autorizaciones de alcance, evaluación
de elegibilidad, jobs, scanner, DNS, ping, HTTP/TCP a objetivos, hallazgos o IA.
La SPA permanece mock-based. La política permite inventariar, nunca escanear.

Se conserva PostgreSQL 17.6, SQLAlchemy/Alembic, sesiones seguras, CSRF,
trusted origins, tenant de ActorContext, RLS, auditoría y outbox existentes.
Los routers nuevos llaman al servicio; el adaptador repository contiene consultas.
No se refactorizan los módulos anteriores ni se alteran sus migraciones.

## Política del operador

`LAB_ASSET_POLICY_JSON` es configuración de proceso, no un campo del cliente ni
una API tenant. Compose la transmite al backend; `.env.example` la deja vacía.
Sin variable, con JSON inválido/incompleto, claves duplicadas, tipos incorrectos,
versión desconocida o allowlist vacía: creación denegada con 403 LAB_POLICY_DENIED.
No se imprime el contenido de una política inválida.

Contrato versión 1 (ejemplo deshabilitado, sin destinos):

```json
{
  "version": 1,
  "allowed_targets": [],
  "excluded_targets": [],
  "max_active_assets_per_tenant": 100
}
```

- Campos obligatorios y sin extras; `version` entero, no booleano ni decimal.
- Allowed/excluded son listas de IPv4 literales canónicas exactas, sin duplicados,
  máximo 10.000 elementos cada una. No CIDR ni nombres, incluso en exclusiones.
- Configuración máxima 350.000 bytes; cuota entera 1–10.000 activos activos/tenant.
- IP privada no implica permiso. Solo coincidencia exacta en allowlist permite
  crear, y exclusiones siempre prevalecen.
- Bloqueo no sobreescribible: 0/8, 127/8, 169.254/16, 224/4 y 240/4.
- El operador debe excluir además las IP exactas de infraestructura/control de
  su laboratorio. No se detectan redes ni se infiere su autorización.
- La misma política de despliegue limita a todos los tenants; sus inventarios se
  aíslan. No representa permiso legal o técnico para ejecutar una prueba de red.
- La política se lee del entorno en cada creación; para cambiar un contenedor,
  el operador debe recrear/reiniciar con su nueva configuración. No se promete
  propagación dinámica ni consenso entre réplicas.
- Lectura, corrección de metadatos y archivo siguen disponibles al retirar un
  objetivo de la allowlist. No amplían alcance ni establecen conexiones.

El hash SHA-256 de la representación tipada de política se conserva en el activo
y en eventos para identificar la configuración aplicada. No es firma ni prueba
de propiedad. Reordenar una lista puede cambiar ese hash sin cambiar sus permisos.

## Contrato HTTP / OpenAPI

Esquema servido: `GET /openapi.json` en desarrollo/test. Artefacto versionado del
subconjunto: `backend/tests/contracts/assets.openapi.json`; prueba de contrato
lo compara con el OpenAPI real normalizando solo el nombre configurable de CSRF.
Todos los endpoints requieren sesión. Mutaciones requieren CSRF; Origin presente
debe ser confiable. No desactivar Secure ni TLS para usar el navegador.

| Operación | Entrada | Éxito y concurrencia |
| --- | --- | --- |
| POST /api/v1/assets | type=ipv4, target, display_name, criticality | 201; Idempotency-Key obligatorio |
| GET /api/v1/assets | page[limit] 1–100 (50 por defecto), page[after], status, criticality, q, type=ipv4 | 200; cursor (created_at,id), filtro tenant siempre |
| GET /api/v1/assets/{id} | UUID | 200; también permite consultar archivados propios |
| PATCH /api/v1/assets/{id} | display_name y/o criticality, no nulos | 200; If-Match obligatorio |
| DELETE /api/v1/assets/{id} | reason no vacío (hasta 500 caracteres) | 204 sin body; If-Match e Idempotency-Key obligatorios |

`If-Match` es versión entera positiva, igual que los endpoints foundation.
No es un ETag HTTP entrecomillado. Versión obsoleta devuelve 409 VERSION_CONFLICT.
PATCH sin campos, IP/tipo/tenant/IDs/estado de propiedad o autoridad del cliente
devuelve 422. Cambiar objetivo implica crear otro activo, no modificar el existente.
IP ambigua, dominio, URL, IPv6 o rango se rechaza sin resolver nada.

Display name: 1–160 caracteres con al menos uno no blanco. Criticality:
low/medium/high/critical. Status de lista: active (default), archived, all.
q busca texto literal en display_name (máximo 100 caracteres, comodines escapados).
Cursor acotado a 512 caracteres, con organización validada; no es una credencial.

Asset incluye id, type, target, display_name, criticality, ownership_status,
version, created_at, updated_at, archived_at, archive_reason, request_id.
Listas: items, page {next_cursor,limit}, request_id. Fechas UTC; IDs UUID.
No se devuelve configuración de política ni claves internas.

Errores con envelope existente: error {code,message,details,request_id}.
401 sesión; 403 permiso/CSRF/Origin/política; 404 recurso ausente o ajeno;
409 duplicado/cuota/versión/archivo/idempotencia; 422 validación; 429 rate limit.
Límites proceso-local por actor/tenant/acción: lectura 90/min, creación/edición
30/min y archivo 20/min; 429 incluye Retry-After. No son cuotas distribuidas.

### Replay HTTP vs outbox

POST y DELETE: Idempotency-Key 1–128 ASCII `[A-Za-z0-9][A-Za-z0-9:._-]*`.
Se guarda solo hash de clave, scope tenant+actor+método+ruta y fingerprint del body
validado (incluye If-Match en DELETE). Duración: 24 horas desde el éxito.
Misma clave/contexto y payload reproduce la respuesta; contenido distinto: 409.
Headers: Idempotency-Replayed true/false y X-Request-ID de la petición actual.
Body de replay de POST conserva request_id y estado de la respuesta original;
GET obtiene el estado actual, incluso si fue archivado después.

Permisos, sesión y CSRF se revalidan antes de replay; POST también revalida política.
Las peticiones fallidas no consumen la clave. Después de 24 horas, el mismo contexto
puede reciclar su registro si una nueva operación válida termina correctamente.
No hay job de purga: expiración funcional no equivale a eliminación de datos.
PATCH no tiene replay; su retry tras éxito debe resolver el 409 y leer el estado.

La clave HTTP nunca se usa como clave outbox. Los eventos internos identifican
acción+activo+versión. Aún no existe publisher ni entrega asíncrona.

## Persistencia, permisos y auditoría

Nueva revisión `20260924_05` después de `20260920_04`:

- assets: unicidad parcial tenant/tipo/IP activa, versión positiva, constraints de
  IPv4 y estado unverified, motivo coherente con archivo, índice de paginación.
- http_idempotency_records: contexto único, fingerprint y hashes, respuesta,
  caducidad y FK compuesta (tenant,asset_id). Actor referencia users globales;
  su membresía activa se valida por el flujo de autenticación existente.
- Ambas tablas con ENABLE/FORCE RLS y USING/WITH CHECK. No contexto: sin acceso.
- Runtime SELECT/INSERT en ambas; UPDATE solo metadatos/versión/archivo del activo
  y columnas de respuesta/expiración del replay. Sin DELETE, ownership, bypass,
  permisos de esquema/globales o cambios a tablas anteriores.
- Trigger impide cambiar identidad/IP/tenant/policy_hash/created_at, restaurar o
  editar archivados y saltar el incremento de versión.
- org_owner y security_manager: create/read/update/archive. analyst/viewer/auditor:
  read. platform_admin conserva permiso global, pero nunca omite política/RLS.

Orden de locks: organización existente, fila cuando corresponde, auditoría.
El lock organizacional serializa cuota, duplicados, replay y cambios concurrentes.
Mutación + audit + outbox + replay se confirman en una transacción; un fallo los
revierte juntos. En asset.created/updated/archived, el evento de auditoría contiene
versión y policy_hash; el payload actual del outbox contiene versión.
No se copia el body, motivo o clave HTTP a audit/outbox. Las denegaciones conservan
errores correlacionados; no se añade un nuevo canal durable de audit de rechazos.

RLS es defensa adicional: las credenciales SQL runtime siguen siendo parte de la
frontera de confianza. La allowlist se evalúa en aplicación, no dentro de SQL.
No se afirma resistencia a un backend/operador/administrador DB comprometido.

Downgrade a 04 solo si assets está vacío; rehúsa perder inventario/historia.
Ensayos exclusivamente en bases efímeras. Nunca vaciar datos para forzar rollback.

## Verificación reproducible

Desde C:\SentinelAI\platform, con Docker disponible:

```powershell
.\scripts\backend-quality.ps1
.\scripts\authenticated-compose-smoke.ps1 -Assets
.\scripts\authenticated-compose-smoke.ps1
git diff --check
```

El primer comando crea PostgreSQL tmpfs, migra y ejecuta Ruff, pytest, cobertura,
pip-audit y secret scan, después elimina su proyecto. La suite incluye fresh/04
con datos/downgrade/re-upgrade/convergencia histórica/grants, idempotencia,
concurrencia, BOLA, CSRF, runtime RLS y rollback.

El smoke -Assets inyecta direcciones de documentación únicamente en su contenedor
efímero; no se guardan como configuración operativa. El smoke normal borra la
política heredada y demuestra denegación por defecto incluso para platform_admin.
Ambos ejecutan API HTTPS y PostgreSQL reales y limpian sus propios recursos.
La CA efímera usa el callback de confianza del smoke existente, nunca configuración
operativa; no se presenta como validación de una PKI de producción.

La prueba de no contacto intercepta resolución y conexión Python durante las cinco
operaciones. PostgreSQL usa libpq. Es evidencia del camino probado y revisión de
código, no una afirmación de captura de paquetes ni certificación general de egress.

## Pendientes fuera de este incremento

Evidencia cifrada y retención, verificación humana de propiedad, aprobaciones de
alcance, elegibilidad, integración UI y posteriormente orquestación simulada.
Escáner real y redes de SENATI requieren otra decisión explícita. Sin declaración
de preparación para producción ni porcentaje prometido de falsos positivos.
