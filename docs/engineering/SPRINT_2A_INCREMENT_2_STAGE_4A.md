# Incremento 2, etapa 4A — política persistente y generaciones

Alcance autorizado: prompt 4A del usuario y continuación del 8 de octubre de 2026.
Checkpoint: `e5e13873dadc2ef79b6f208169115680a83fbcc1`.
Se conserva [ADR-009 laboratorio](ADR_009_LAB_STORAGE.md). Este documento concreta
4A; no atribuye aprobación institucional, personas responsables ni objetivos reales.
El diseño anterior de etapa 4 mencionado por el prompt no estaba disponible como
documento separado en el checkout ni en el contexto recuperado. No se afirma haberlo
auditado: las decisiones implementadas aquí siguen las invariantes explícitas de 4A.

## Autoridad única y frontera

PostgreSQL es la única autoridad de admisión. `LAB_ASSET_POLICY_JSON` es entrada
opcional del CLI local de publicación, nunca configuración del runtime. Se retiró
su propagación al backend en Compose. Arranque y migraciones NO importan políticas.
Sin publicación: nuevas altas de activos y presentaciones de evidencia denegadas.
Una publicación con allowlist vacía retira la admisión; no se borra la revisión.

Se reutiliza `LabPolicy`: versión entera 1, IPv4 exactas/manuales, listas únicas de
hasta 10.000 entradas y cuota activa 1–10.000. Exclusiones ganan a inclusiones y
persisten los bloqueos 0/8, loopback, link-local, multicast y direcciones reservadas
altas. Una dirección privada no implica autorización. No se resuelve DNS ni se
contactan objetivos. Los ejemplos/pruebas usan exclusivamente fixtures sintéticos.

Ni admisión ni evidencia significan propiedad o permiso de escaneo.
`ownership_status=unverified`, IP inmutable y permisos/CSRF originales permanecen.
No hay solicitudes, decisiones humanas, verificación, evaluate, scanner, UI,
workers ni publisher de outbox. El publicador de **política** no es un dispatcher.

## Datos, generaciones y migración 08

01–07 y snapshots entregados no se modifican. 08 añade:

| Objeto | Función |
| --- | --- |
| `policy_publisher_tenants` | Asignaciones administrativas LOGIN/tenant |
| `lab_policy_revisions` | UUID de publicación, secuencia por tenant, snapshot, hash, procedencia, LOGIN y fecha |
| `lab_policy_current` | Puntero tenant/revisión vigente, con FK compuesta |
| `asset_admission_history` | Historial inmutable tenant/activo/generación, estado, revisión causante, motivo y fecha |
| `asset_admission_current` | Puntero a la generación exacta mediante FK compuesta |
| `evidence_operations.admission_generation` | Generación fijada al reservar; FK tenant/activo/generación |

Snapshot canónico: serialización JSON del contrato `LabPolicy`, campos en su orden
declarado, sin espacios, preservando orden de las listas. SHA-256 de esos bytes.
La validación SQL comprueba el contrato y esos bytes además de la validación Python;
no se normalizan dominios, formas ambiguas o IPs inválidas. Reordenar una lista puede
cambiar el hash pero NO cambia por sí solo la admisión ni la generación.

- Activos existentes al migrar: generación 0, denegada, motivo `legacy`, sin revisión
  inventada. Evidencia antigua conserva su recibo y generación NULL histórica.
- Alta posterior a publicación: generación 1 admitida, ligada a la revisión vigente.
- Una transición admitido → denegado o denegado → admitido incrementa la generación.
- Retirar y readmitir produce 1 → 2 → 3 aunque la tercera política repita el primer
  hash. No depende de consultas intermedias, cachés ni del proceso API.
- Cambiar cuota, listas sin afectar esa IP o metadatos del activo no cambia generación.
  Reducir cuota bloquea nuevas altas según el recuento existente, no retira activos.
- Archivar un activo admitido añade transición denegada. Un activo ya denegado no
  fabrica otra retirada. Archivo sigue permitido sin admisión y no se revierte.
- Revisión y transiciones son append-only: sin UPDATE/DELETE concedidos, con triggers
  de inmutabilidad. Los punteros solo los mantienen triggers cerrados.

Operaciones de evidencia nuevas requieren generación positiva. Las pendientes de
antes de 08 no se adoptan: al no tener contexto nuevo no pueden confirmar mediante
el servicio; se inspeccionan/reconcilian con el mantenimiento existente. Las
confirmadas antiguas siguen disponibles para lectura histórica autorizada, pero
no se convierten en una nueva presentación mediante replay.

Downgrade 08 rechaza cualquier política publicada; no destruye esa historia para
facilitar rollback. Sin publicaciones permite retirar solo las estructuras nuevas,
conservando activos/evidencia legacy. Conserva el rol NOLOGIN de ámbito cluster.

## Credencial y protocolo operacional

El administrador provisiona un LOGIN dedicado, sin ownership, DDL, SUPERUSER,
BYPASSRLS, CREATEDB, CREATEROLE ni membresías adicionales. Hereda exclusivamente
`sentinelai_policy_publisher` (rol colectivo NOLOGIN). No reutilizar runtime,
migrator ni `sentinelai_evidence_maintenance`. No asignar el rol colectivo como
`role_name`: asignar el LOGIN concreto en `policy_publisher_tenants` para cada
organización ya existente. El CLI no crea organizaciones, roles ni asignaciones.
Retirar/renombrar una credencial requiere retirar sus asignaciones administrativamente.

Todas las tablas nuevas usan ENABLE/FORCE RLS. Runtime solo lee su contexto tenant;
no publica ni altera asignaciones/punteros/historial. Mantenimiento no publica.
El publicador solo lee sus asignaciones y sus tenants; INSERT de revisión es la
única escritura de política concedida. Auditoría/outbox tienen políticas restrictivas
adicionales de tenant provisionado y acción `lab_policy.published`. Se permite leer
la cadena auditada para encadenar su nuevo evento, no documentos de evidencia,
sesiones ni identidades. No se conceden permisos RBAC humanos nuevos.

Los triggers SECURITY DEFINER tienen entradas cerradas, SQL sin interpolación de
identificadores y `search_path=pg_catalog,public,pg_temp`; no se concede EXECUTE al
público. El trigger de publicación comprueba el LOGIN de `session_user`, membresías,
asignación y contrato, además de RLS. El administrador/migrator que posee el esquema
permanece dentro de la frontera de confianza; esto no protege de un administrador DB
malicioso que desactive triggers o cambie grants.

Desde `backend`, con una credencial operacional preaprovisionada en
`LAB_POLICY_PUBLISHER_DATABASE_URL` y un JSON candidato explícito en
`LAB_ASSET_POLICY_JSON` (no guardar secretos en Git, paquetes ni argumentos CLI):

```text
python -m app.assets.publisher --organization <UUID asignado>
python -m app.assets.publisher --organization <UUID asignado> --publish --expected-sequence <N> --publication-id <UUID nuevo> --provenance <referencia operacional breve>
```

Primera orden: valida candidato e inspecciona revisión actual, sin escribir. Revisar
el JSON completo, hash y conteos antes de publicar. Procedencia: 1–160 caracteres
ASCII acotados; sin secretos ni afirmaciones de aprobación humana/institucional.
Guardar de forma operacional el UUID, JSON exacto, procedencia y secuencia esperada.
Segunda orden: publicación explícita con comparación de secuencia. Conflicto requiere
reinspección y decisión del operador, no un reintento automático con otra secuencia.

Si el commit retorna error, su resultado puede ser incierto. Repetir **el mismo** UUID,
candidato, procedencia y secuencia resuelve el recibo sin duplicar eventos. Cambiar
payload con el mismo UUID da conflicto. Un replay de revisión histórica devuelve
recibo histórico: nunca repone el puntero ni reactiva admisiones.

## Transacciones, concurrencia e I/O

Publicación: lock organizacional existente (seed 1), revisión/secuencia, actualización
de punteros y transiciones, lock de auditoría (seed 0), auditoría/outbox, un commit.
Un constraint trigger diferido impide confirmar revisión sin ambos eventos.
Los triggers de INSERT de auditoría/outbox comparan el JSON completo obligatorio
(`policy_hash`, `sequence`, `publisher`, `provenance`) con la revisión tenant/UUID.
El principal autoritativo es `session_user`, LOGIN PostgreSQL autenticado y
provisionado; no un usuario humano declarado en JSON ni el rol usado por
SECURITY DEFINER. La revisión no permite suministrar `publisher` o `published_at`.
La procedencia es declarativa: se valida su formato y coincidencia, no su veracidad
institucional. Auditoría exige actor `system`, sin `actor_user_id`, outcome success
y request_id igual al UUID; outbox exige la clave interna `lab_policy:<UUID>`.
Contenido extra, omitido o contradictorio da `23514/policy_event_content`;
referencia inexistente/cruzada o principal ajeno, `23514/policy_event_authority`.
Índices únicos impiden duplicados, incluso en otra transacción; la falta de eventos
al commit da `23514/policy_events_required`. El publicador solo tiene INSERT por
columnas en los eventos: no puede suministrar `published_at` ni `attempts`, ni
UPDATE/DELETE. El ORM utiliza el default SQL preexistente 0 para attempts, sin
ampliar grants ni modificar 01–07. Se conserva el trigger de secuencia/predecesor
y el hash/verify_chain existentes; no se atribuye al trigger nuevo un recálculo SQL
del hash criptográfico. No se añade dispatcher ni se prueba entrega del outbox.
Fallo en cualquier fase revierte también admisiones. No hay filesystem ni red de
objetivos durante esta transacción. Altas/metadatos/archivo y publicación comparten
el lock organizacional; una alta no puede quedar sin contexto de admisión.

Evidencia conserva coordinador local → organización → activo → cuota → operación
→ auditoría. Cifrado/storage se ejecutan fuera de transacciones DB. Cada fase nueva
reautentica y consulta autoridad persistida; después del I/O se compara generación,
no solo hash. Antes de retornar el HTTP se comprueba otra vez. No se compensan
archivos ante commit incierto: sigue aplicando el reconciliador conservador de etapa 2.

Retirada bloquea presentaciones/replays y resultados nuevos, no lectura histórica
autorizada. Una retirada después de commit pero antes de respuesta puede devolver
denegación aunque exista evidencia histórica confirmada; consultarla no acredita
admisión actual. Tras readmisión, nueva presentación usa nueva clave/generación.

## Preparación para 4B y límites

`current_policy`, `require_target` y `require_admission` son servicios internos;
deben invocarse dentro del lock/contexto autenticado correspondiente. No son API
pública ni tokens de autorización. Contratos OpenAPI de activos/evidencia conservados.

4B deberá ligar cada solicitud/decisión a tenant, activo, versión de evidencia y
generación de admisión; comprobar admisión vigente y misma generación al resolver
y al usar el resultado. Retirada/readmisión exige nueva revisión humana separada
del presentador. **No existe todavía implementación de invalidación de solicitudes**,
ni decisiones humanas, vigencia de verificación o autorización de escaneo.

Laboratorio Linux local: mismas limitaciones de storage, retención, backups, llaves
y durabilidad física de etapas anteriores. Las pruebas tmpfs no demuestran pérdida
de energía, despliegue productivo ni permisos para escanear redes reales.
Resultados y reproducción: [handoff 4A](SPRINT_2A_INCREMENT_2_STAGE_4A_HANDOFF.md).
