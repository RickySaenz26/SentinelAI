# Incremento 2, etapa 2 — protocolo interno de evidencia

Checkpoint: 894268192b1b5de96b010eed6216b82483d7b6fa. Especialización de
ADR_009_LAB_STORAGE.md; no endpoints, revisión humana ni permiso de escaneo.

## Invariantes y estados (definidos antes de implementar)

- Solo una sesión autenticada vigente determina el tenant. Se reutiliza get_actor
  en transacciones nuevas; nunca se conserva autoridad de un ActorContext tras I/O.
- Una serie de versiones pertenece a un activo: dossier_id del envelope = asset_id.
  No se inventa una entidad de auditoría o autorización. Versiones confirmadas son
  inmutables y siguen significando evidencia almacenada, no control aceptado.
- reserved -> prepared -> committed, o reserved/prepared -> reclaimed -> aborted.
  reclaimed es un fence irreversible: nunca puede confirmar una versión. Solo el
  mantenedor puede reclamar/abortar; una reserva no es una referencia aceptada.
- La reserva cuenta un objeto y 24 KiB contra la cuota; confirmación convierte la
  reserva a uso real. Liberación solo después de limpieza segura o ausencia probada.
- Clave de replay por tenant/usuario, con hash de clave y fingerprint del contenido;
  no se guardan documento, cookies, CSRF ni respuestas sensibles. Un replay confirmado
  devuelve solo el recibo y no repite auditoría/outbox. Una operación pendiente no
  se reejecuta ciegamente: requiere reconciliación, sin promoción de huérfanos.
- La cabecera cifrada (key_id, wrapper, nonce y hash) se registra en prepared para
  demostrar pertenencia del objeto antes de publicarlo; todavía no es una versión
  confirmada. Referencia de versión, cuota, estado committed, auditoría y outbox se
  confirman juntos. El wrapper queda vinculado a esa referencia por la operación
  inmutable. Cualquier excepción de commit se trata como resultado incierto:
  el escritor no elimina archivos ni cancela automáticamente la reserva.

## Orden y fases

1. Lock local de coordinación, exclusivo y no bloqueante, en el directorio auditado.
2. Transacción breve: get_actor (advisory lock de organización), activo, cuota,
   operación; validar permiso evidence:write explícito, política, versión, replay y
   cuota. Persistir reserva y cerrar transacción.
3. Cifrar mediante prepare del adaptador fuera de DB. Transacción breve para asociar
   a la reserva el recibo y cabecera cifrada; cerrar antes de publicar con write.
4. Transacción nueva: reautenticar, revalidar activo/política/versión/reserva y plazo;
   confirmar versión, cuota y eventos (audit advisory lock al final). Commit.

El lock local precede cualquier lock DB y se mantiene durante la operación. El
lock interno .evidence.lock solo se usa sin transacción DB activa. Una única raíz
Linux local compartida es requisito: no hay coordinación entre hosts, copias de
volúmenes ni writers ajenos al coordinador. El costo deliberado es serializar las
operaciones de evidencia de esa raíz; los endpoints existentes no esperan ese lock.

## Mantenimiento

Inspección por defecto; ejecución explícita. Credencial distinta con rol NOLOGIN
sentinelai_evidence_maintenance, sin runtime/migrator, sin BYPASSRLS/DDL/ownership.
Tenant operacional explícito y provisionado por administrador en
evidence_maintenance_tenants(role_name, organization_id). RLS forzado exige tanto
el tenant del contexto como una asignación para current_user; la credencial solo
puede leer sus asignaciones y no insertarlas, actualizarlas ni borrarlas. Runtime
no tiene acceso a esa tabla. Las políticas de runtime y mantenimiento son distintas.
Cambiar el UUID del CLI o app.organization_id mediante SQL directo no concede acceso.
No se enumeran tenants ni se otorgan privilegios globales de identidad/autenticación.
La puesta en operación requiere aprovisionar esa credencial y su alcance fuera de
la aplicación; las pruebas crean solamente credenciales y directorios desechables.
Cada LOGIN operacional debe ser dedicado, sin privilegios adicionales ni membresía
en runtime, sin ownership, DDL, CREATEROLE, CREATEDB, SUPERUSER o BYPASSRLS. Hereda
únicamente sentinelai_evidence_maintenance. Las asignaciones usan el LOGIN concreto,
nunca el rol colectivo; al retirar/renombrar una credencial se retiran sus asignaciones.
El CLI rechaza SET ROLE, runtime y migrator. El trigger reserva prepared/committed
al escritor; mantenimiento solo puede pasar operaciones vencidas a reclaimed y
después a aborted. No puede insertar referencias ni modificar wrappers o cuota usada.

Con el mismo lock local, reclamar una reserva vencida y sin referencia dentro de
DB, confirmar el fence y cerrar DB antes de filesystem. Validar pertenencia por
recibo persistido, hash/contexto y archivo privado; nunca borrar finales corruptos,
referenciados o desconocidos. Temporales incompletos solo de reservas conocidas.
Después de limpiar y sincronizar, una transacción libera cuota y marca aborted.
Interrupciones dejan reclaimed para reintentar. Inspección no promueve ni elimina.
Un error de fsync mantiene reclaimed y la cuota reservada. En el reintento se
sincroniza el directorio incluso si el unlink previo ya dejó ausente el objeto.
Solo un fsync que retorna y el posterior commit permiten informar cleaned.
Un fsync realizado cuya respuesta se pierde también se considera incierto y se
reintenta; no se interpreta ausencia del archivo como sincronización exitosa.

## Migración 06 y operación local

20261005_06 desciende de 20260924_05, sin editar revisiones 01–05 ni snapshots.
Añade evidence_operations, evidence_versions, evidence_quotas y la tabla mínima
de asignaciones operacionales. Todas tienen RLS forzado. Las FK compuestas atan
operación/activo/tenant/versión/estado y membresía/actor/tenant. Las referencias
solo apuntan al estado committed; runtime no tiene UPDATE/DELETE de referencias.
No se usa una FK tenant/sesión mutable que impida el cambio legítimo de organización:
session_id se conserva para la reautenticación y comparación del escritor.

Cuota inicial: 1.000 objetos y 16 MiB por tenant, solo administrativamente ajustable.
Reserva: 24 KiB y un objeto, plazo de cinco minutos; una reserva activa por activo.
No hay limpieza automática basada solo en antigüedad. El downgrade rechaza cualquier
operación existente; en bases vacías retira tablas, permiso evidence:write y trigger.
Conserva el rol NOLOGIN de ámbito cluster y su USAGE de public para no afectar otras
bases. El rol debe ser provisionado/revisado por administración; nunca por la web.

El permiso evidence:write se asigna solamente a org_owner y security_manager.
Se exige explícitamente, sin interpretar platform:admin como aprobación ni escaneo.
ownership_status permanece unverified. Los recibos no son tokens de autorización.

Ejecutar en Linux soportado con roots privados y KEK externa ya configurados:

```text
LAB_EVIDENCE_MAINTENANCE_DATABASE_URL=<credencial operacional provisionada>
python -m app.evidence.maintenance --organization <UUID asignado> --repository-root <checkout>
python -m app.evidence.maintenance --organization <UUID asignado> --repository-root <checkout> --execute
```

La primera orden solo inspecciona. Las variables LAB_EVIDENCE_ROOT,
LAB_EVIDENCE_KEY_ROOT y LAB_EVIDENCE_ACTIVE_KEY_ID siguen siendo explícitas. No
provisionar llaves ni credenciales desde el CLI. El aprovisionamiento de asignaciones
es una operación administrativa SQL; ni runtime ni mantenimiento pueden ampliarlas.

Retención de evidencia confirmada, backups, rotación operacional, workflow humano,
workers y publisher siguen pendientes. Las pruebas tmpfs y fallos de proceso no
demuestran durabilidad física ante pérdida de energía.
