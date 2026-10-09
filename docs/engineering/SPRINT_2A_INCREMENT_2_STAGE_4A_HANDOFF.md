# Handoff de etapa 4A — lista para auditoría independiente

Checkpoint conservado: `feature/sprint-2a-assets-scope`,
`e5e13873dadc2ef79b6f208169115680a83fbcc1`. Sin stage/commit/push/merge.
Fecha de reanudación: 2026-10-08 (America/Lima).

Guía técnica: [política y generaciones](SPRINT_2A_INCREMENT_2_STAGE_4A.md).
Las secciones históricas siguientes describen la validación anterior a la auditoría
P2/P3. No acreditan por sí mismas el diff corregido. La remediación y su evidencia
nueva se registran al final; no constituyen aprobación productiva ni cierre de Sprint 2A.

## Recuperación e historia de ejecuciones

- Antes de editar se verificaron rama/HEAD/origin y árbol/índice limpios contra el
  checkpoint del prompt. La continuación admite y conserva el diff pendiente.
- Reanudación: Git confirma misma rama/HEAD, índice vacío, 14 rutas tracked
  modificadas y 4 nuevas en ese instante. El contador UI no se usó como inventario.
- Logs previos completos: primera dirigida 120 passed/1 failed; segunda 202 passed/
  2 failed. Eran fixtures de segundo tenant sin publicación, no gates finales.
- Los tres fixtures ya publican mediante el LOGIN operacional efímero. Conservan
  la comprobación original de FK/SQLSTATE; no se sustituyen por una denegación previa.
- No había runners activos al reanudar. Quedaba solo PostgreSQL del proyecto propio
  `sentinelai-stage4a-dev`. Su esquema estaba vacío; la primera prueba al reanudar
  falló en setup (`roles` ausente), no ejecutó casos de seguridad. Se aplicaron 01–08
  explícitamente en ese proyecto. No se presupone persistencia de tmpfs entre reinicios.
- Después de inicializar: 17 pruebas afectadas PASS, exit 0. Registro
  `audit-results/stage4a-resumed-affected-v2.log`. Resultado intermedio: después se
  añadieron pruebas y restricciones de inserción de generación.

## Estado de validación

No reutilizar resultados de etapas anteriores como ejecución de este diff.

### Implementación y trazabilidad

- Autoridad compartida `app/assets/authority.py`; ningún consumidor runtime importa
  la configuración del entorno. El CLI `app/assets/publisher.py` es el único consumidor
  productivo de esa entrada. Compose no la propaga al backend.
- 08: cinco tablas RLS, historial/punteros, contexto de generación en evidencia,
  grants de publicador separados, INSERT de revisión limitado por columna (LOGIN y
  fecha no suministrables), FK compuestas, triggers cerrados y constraint de eventos.
- La publicación no necesita llaves ni storage. Queda serializada con altas/archivo
  mediante el lock organizacional. Revisiones/admisiones/eventos confirman juntos.
- Evidencia revalida tras I/O y antes del retorno HTTP. Replays de generaciones
  retiradas no recuperan autoridad. Consulta histórica/archivo siguen disponibles.
- Sin decisiones humanas ni escaneo. `ownership_status=unverified` se conserva.

### Validaciones históricas anteriores a P2/P3

| Comando / alcance | Exit | Resultado y registro bajo `audit-results/` |
| --- | --- | --- |
| `stage4a-dev.ps1 -Phase reset` | 0 | Instalación 01–08 en tmpfs; `stage4a-resumed-reset.log` |
| `pytest tests/test_assets.py tests/test_evidence_service.py tests/test_evidence_api.py tests/test_policy_authority.py tests/test_policy_migration.py tests/test_asset_migration.py tests/test_evidence_migration.py tests/test_migration_convergence.py -q -p no:cacheprovider --no-cov --tb=short` | 0 | 220 passed; `stage4a-resumed-targeted-final.log`; anterior al ajuste final de grants por columna, cubierto luego por gate completo |
| `pytest tests/test_evidence_api_migration.py -q -p no:cacheprovider --no-cov --tb=short` | 0 | 1 passed; `stage4a-permission-migration-fix.log` |
| `docker run --rm --network none ... pytest storage_tests -q -p no:cacheprovider --no-cov` | 0 | 95 passed; `stage4a-final-storage.log`, marcador `STAGE4A_STORAGE_EXIT=0` |
| `authenticated-compose-smoke.ps1 -Evidence` | 0 | Publicación explícita, 5 operaciones de evidencia, activos, CSRF/RBAC/RLS/replay; `stage4a-final-https-evidence.log` |
| `authenticated-compose-smoke.ps1` | 0 | Sesiones y denegación de alta sin política publicada; `stage4a-final-https-default.log` |
| Imagen quality sin red: validadores OpenAPI actuales + Ruff del helper + `pip check` | 0 | Assets=5, evidence=5, snapshots intactos; `stage4a-final-contract-v2.log` |
| `test-package-file-policy.ps1` | No corroborado | El informe anterior declaró exit 0, pero `stage4a-final-exclusions.log` no existe. No se reconstruye ese log ni se considera evidencia verificable. |

Los comandos pytest dirigidos se ejecutaron con `docker compose -p
sentinelai-stage4a-dev -f compose.quality.yaml run --rm -T --no-deps -v
"${PWD}/backend:/workspace:ro" -w /workspace quality ...`. Los runners sin red usan
la imagen `sentinelai-closure-quality:local`, `--read-only` y tmpfs privado de UID
10001; nada se escribe a un volumen operacional. Los logs históricos conservan
salida parcial y errores intermedios. La captura HTTPS omitió el flujo informativo:
no se consideran transcripciones completas ni se reconstruyen retrospectivamente.

Primer gate completo: 577 passed/1 failed, exit 1 (`stage4a-final-gate.log`). Falló
la prueba histórica de 07 que subía a `head` y exigía esquema sin cambios: con 08
esa premisa dejó de corresponder al propósito de 07. Se fijó el destino a 07 sin
quitar aserciones. La convergencia y preservación de 08 tienen pruebas separadas.
También hubo un intento auxiliar con un antiguo `tests/contract_smoke.py` inexistente
(exit 2): se sustituyó por los validadores actuales, que pasaron sin tocar snapshots.
No se presentan esas ejecuciones intermedias como aceptación final.

### Gate histórico de 578 pruebas (anterior a P2/P3)

Comando: `.\scripts\backend-quality.ps1`, con salida completa en
`audit-results/stage4a-final-gate-v2.log`. Exit 0 y marcador
`STAGE4A_FINAL_GATE_EXIT=0`. Imagen reconstruida con las fuentes definitivas; proyecto
independiente `sentinelai-closure-quality-691dfef982`, no una ejecución del checkout
histórico ni de una imagen anterior a la última corrección.

| Comprobación | Exit | Resultado final |
| --- | --- | --- |
| Alembic `upgrade head` | 0 | Instalación limpia 01–08 |
| Ruff `format --check` | 0 | 101 archivos conformes |
| Ruff `check` | 0 | Sin findings |
| pytest completo, PostgreSQL real/runtime restringido | 0 | 578 ejecutadas, 578 passed, 0 failed, 0 skipped |
| Cobertura de sentencias | — | 99,35% |
| Cobertura de ramas | — | 96,73%, requisito >=90% intacto |
| `pip check` | 0 | No broken requirements found |
| `pip-audit --progress-spinner=off -r requirements.txt` | 0 | No known vulnerabilities found; versión 2.10.1 |
| Secret scan del gate | 0 | Sin patrones de alta confianza |
| `git diff --check` | 0 | Sin errores de whitespace |
| Índice y migraciones entregadas/snapshots | 0 | Índice vacío; 01–07 y snapshots sin cambios |

El gate incluye los nuevos casos de roles SQL, retorno al mismo hash con generaciones
1/2/3, proceso nuevo, concurrencia, rollback/commit incierto, rechazo post-I/O,
preservación histórica, upgrade con datos de 07, convergencia schema/grants y downgrade.
Los tres warnings son Starlette/httpx y los dos runpy de bootstrap/onboarding; no se
suprimieron. Cobertura combinada que imprime pytest (98,92%) no se presenta como
cobertura de ramas: las dos métricas separadas constan arriba.

### HTTPS: evidencia verificable y alcance

Corrección P3: los logs conservados NO contienen `AUTHENTICATED COMPOSE SMOKE: PASS`.
Sí contienen `STAGE4A_HTTPS_EVIDENCE_EXIT=0` / `STAGE4A_HTTPS_DEFAULT_EXIT=0`.
No se recuperó una transcripción original completa. La afirmación previa sobre el
marcador del script era incorrecta; la nueva aceptación se ejecuta por separado.
El de evidencia incluye `SMOKE_POLICY_PUBLICATION=PASS`,
`SMOKE_POLICY_CREDENTIAL_CLEANUP=PASS` y `HTTPS_EVIDENCE_RECORDS=PASS`.
El LOGIN publicador se crea en el DB exclusivo del smoke, publica por el servicio
restringido y se elimina con su asignación; nunca se usa migrator para publicar.
Las llaves/objetos quedan en tmpfs del backend efímero. El frontend participa como
proxy HTTPS; esto no implementa ni acepta una UI de evidencia/verificación humana.

Esa ejecución precede a las correcciones SQL de P2; no acredita el código corregido.

### Inventario Git histórico del diff (antes de P2/P3)

19 rutas tracked modificadas:

- `.env.example`, `README.md`, `compose.yaml`.
- `backend/app/api/v1/assets.py`, `backend/app/assets/service.py`.
- `backend/app/evidence/access.py`, `backend/app/evidence/service.py`.
- `backend/quality_gate.py`, `backend/tests/conftest.py`.
- `backend/tests/test_asset_migration.py`, `backend/tests/test_assets.py`.
- `backend/tests/test_evidence_api.py`, `backend/tests/test_evidence_api_migration.py`.
- `backend/tests/test_evidence_migration.py`, `backend/tests/test_evidence_service.py`.
- `scripts/authenticated-compose-smoke-helper.py`, `scripts/authenticated-compose-smoke.ps1`.
- `docs/engineering/SPRINT_2A_INCREMENT_1.md`, `docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_3.md`.

7 rutas nuevas, sin staging:

- `backend/alembic/versions/20261007_08_persistent_lab_policy.py`.
- `backend/app/assets/authority.py`, `backend/app/assets/publisher.py`.
- `backend/tests/test_policy_authority.py`, `backend/tests/test_policy_migration.py`.
- `docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_4A.md` y este handoff.

`git diff --stat` solo cuenta tracked: no omitir los siete archivos nuevos al auditar.
01–07 y snapshots históricos/OpenAPI no tienen diff. No se redujeron thresholds,
reglas Ruff, ni aserciones de seguridad/FK para obtener PASS.

Resumen tracked final: 19 archivos, 250 inserciones y 55 eliminaciones; los siete
untracked se enumeran arriba. Rama, HEAD y referencia local origin coinciden con
el checkpoint. No se volvió a publicar ni se afirma un fetch remoto nuevo al cierre.

## Cleanup histórico comprobado

La consulta final por etiquetas exactas dio 0 contenedores, 0 redes y 0 volúmenes
para cada proyecto:

- `sentinelai-stage4a-dev`.
- `sentinelai-closure-quality-71ef15f1d1` (primer gate).
- `sentinelai-closure-quality-691dfef982` (gate definitivo).
- `sentinelai-smoke-24002a40e2c91ea6` (HTTPS evidencia).
- `sentinelai-smoke-61be3557f38ce340` (HTTPS sin política).

Ausentes runners `sentinelai-stage4a-storage` y `sentinelai-stage4a-contract` y
procesos de validación. Consulta exit 0; registros `stage4a-final-cleanup.json` y
`stage4a-final-cleanup-marker.log`, marcador `STAGE4A_CLEANUP_AND_PROCESSES=PASS`.
El proyecto dev se retiró explícitamente al terminar sus pruebas (`stage4a-dev-cleanup.log`,
exit 0). Los otros scripts completaron su cleanup propio.

Los fixtures eliminan LOGIN/asignaciones y sus roots/llaves temporales; el smoke
confirma eliminación de su credencial publicadora y después de contenedores/volúmenes.
Esto elimina sus DB, storage y KEK efímeros; no afirma borrado seguro de memoria física.
No se inspeccionaron, retiraron ni limpiaron volúmenes/credenciales operativos.
Imágenes y cachés normales de build se conservan. `audit-results` sigue ignorado y
excluido por el predicado de paquete; no se construyó ningún ZIP.

## Límites y siguiente paso

Lista para auditoría independiente de **4A**, no para producción ni para escanear
redes reales. No hay personas que hayan verificado evidencia, decisiones humanas,
autorizaciones, integraciones SENATI, scanner, UI, workers ni publisher de outbox.
No se declara invalidación de solicitudes existentes: aún no hay solicitudes.
4B deberá usar las generaciones para impedir reactivación de revisiones humanas.
Las limitaciones Linux local, tmpfs frente a pérdida de energía, backups/restore,
retención y rotación operacional de llaves permanecen explícitas en la guía.

La afirmación histórica de cierre queda sustituida por los resultados posteriores a
P2/P3 al final de este documento. No se ejecuta 4B ni se hace commit automáticamente.

## Reanudación segura

Inspeccionar Git/diff/logs y procesos antes de ejecutar. No restaurar ni recrear rama.
`audit-results` permanece ignorado por Git y excluido del paquete; no generar ZIP.
El runner local `audit-results/stage4a-dev.ps1` describe el proyecto de pruebas.
No eliminar proyectos SentinelAI operativos ajenos. Limpiar solo nombres exactos
registrados por esta aceptación y verificar ausencia mediante etiquetas Docker.

No iniciar 4B. El siguiente paso tras cerrar validación es auditoría independiente.

## Remediación focalizada P2/P3 — 2026-10-09

Esta sección sustituye las afirmaciones de aceptación anteriores para el diff
corregido. Al comenzar se comprobó `feature/sprint-2a-assets-scope`, HEAD
`e5e13873dadc2ef79b6f208169115680a83fbcc1`, 19 modificados, 7 nuevos e índice vacío.
La referencia **local** `origin/feature/sprint-2a-assets-scope` coincide; no se hizo
fetch ni se atribuye una verificación remota nueva. Se conservaron todos los cambios.
No había runners 4A activos al comenzar; los stacks operativos se dejaron intactos.

### P2: defensa de PostgreSQL, no solo del CLI

Cambios focalizados:

- `backend/alembic/versions/20261007_08_persistent_lab_policy.py`: INSERT por columna
  para eventos; sin privilegio de insertar `published_at`/`attempts` en outbox.
  Triggers de validación en ambos tipos de evento, unicidad tenant/revisión y
  constraint diferido con nombre explícito. Downgrade revoca también ACL por columna.
- `backend/app/platform/database/models.py`: `OutboxEvent.attempts` usa el default
  **SQL ya existente** 0 en lugar de emitir ese campo desde el cliente. No requiere
  ampliar permisos ni alterar migraciones entregadas.
- `backend/tests/test_policy_event_integrity.py`: 29 casos nuevos de SQL directo,
  preparados con filas válidas y LOGIN restringido. La auditoría usa el helper
  existente para generar una cadena válida; no llama al CLI/PolicyPublisher para
  insertar la revisión o validar sus contenidos. Outbox/revisión se insertan con SQL.
- `backend/tests/test_policy_authority.py`: la ausencia de eventos exige ahora
  `23514/policy_events_required`, además de comprobar rollback.
- `backend/tests/test_policy_migration.py`: downgrade comprueba que no persista
  privilegio INSERT por columna del publicador sobre auditoría/outbox.
- Guía 4A y este handoff: contrato de identidad, nuevas defensas y corrección P3.

La revisión fija el principal a `session_user` (LOGIN autenticado, provisionado y
restringido). Ni un JSON ni `SET ROLE` pueden sustituir el LOGIN de la conexión.
`provenance` es una referencia **declarada**, no una acreditación institucional:
debe coincidir exactamente entre revisión y eventos. Hash y secuencia se comparan
contra los valores persistidos de la revisión; publisher también, y el JSON debe
contener exactamente los cuatro campos contractuales. El identificador de petición
y la clave interna del outbox quedan ligados al UUID de revisión.

| Ataque / condición | Resultado SQL comprobado |
| --- | --- |
| Hash, secuencia, procedencia, publisher o campo extra contradictorio en cualquiera de los eventos | `23514`, `policy_event_content` |
| UUID inexistente, referencia a revisión de otro tenant o runtime intentando atribuirse al publicador | `23514`, `policy_event_authority` |
| `request_id` / clave interna desligada del UUID | `23514`, `policy_event_content` |
| Outbox con `published_at` o `attempts` suministrado | `42501`, permiso de INSERT de tabla/columna denegado |
| Falta auditoría, outbox o ambos | Al commit: `23514`, `policy_events_required` |
| Auditoría duplicada, también en transacción posterior | `23505`, `uq_policy_audit_revision` |
| Outbox duplicado, también en transacción posterior | `23505`, índice preexistente `uq_outbox_organization_idempotency_key`; además existe unicidad por revisión |

El control positivo confirma cadena auditada válida, publisher autenticado,
outbox pendiente (`published_at=NULL`, `attempts=0`) y lectura aislada por tenant.
Los negativos comprueban ausencia de filas confirmadas; una retirada SQL fallida
restaura también puntero/generación y conteos de eventos. La secuencia/predecesor
de auditoría sigue protegida por el trigger de 04 y `verify_chain` no se debilitó.
No se afirma recálculo criptográfico del hash en SQL ni resistencia a un DBA malicioso.
No se añadieron grants globales para runtime/mantenimiento ni un dispatcher outbox.

### P3: procedencia de los registros

Los logs anteriores `stage4a-final-https-*.log` se conservaron sin alterarlos.
Contienen los marcadores de exit del wrapper, pero no el PASS final del script.
No se localizó otra captura original completa y no se reconstruyó. El archivo
`stage4a-final-exclusions.log` sigue ausente: se retiró su cita como evidencia.
Los resultados históricos de **578** pruebas permanecen identificados como anteriores
a P2/P3, no como aceptación del código corregido.

Las nuevas ejecuciones usan `audit-results/stage4a-p2-validate.ps1`: lanza un proceso
`pwsh -NoProfile -File ...`, captura **todos** los flujos (`*>&1 | Tee-Object`), toma
`$LASTEXITCODE` inmediatamente y exige el PASS emitido por cada smoke. No se altera
el script productivo para fabricar marcadores. Los artefactos son locales/ignorados,
no se agregan a Git ni al paquete.

### Ejecuciones nuevas: comandos, resultados y logs

Directorio de ejecución: `C:\SentinelAI\platform`. Prefijo de pruebas dirigidas:
`docker compose -p sentinelai-stage4a-p2 -f compose.quality.yaml run --rm -T --no-deps
-v "${PWD}/backend:/workspace:ro" quality`. La credencial admin solo migra el DB
desechable; las pruebas publican mediante el LOGIN restringido.

| Comando / alcance | Exit | Resultado / archivo en `audit-results/` |
| --- | --- | --- |
| Proyecto P2: `up -d --wait postgres`; `run ... -e DATABASE_URL=<migrator de closure_test> quality alembic upgrade head` | 0 | Instalación 01–08; `stage4a-p2-migrate.log` |
| `pytest tests/test_policy_event_integrity.py tests/test_policy_authority.py tests/test_policy_migration.py -q -p no:cacheprovider --no-cov --tb=short` (v1) | 1 | 25 errores del nuevo fixture estricto antes de SQL, 22 passed; `stage4a-p2-targeted-v1.log` |
| Misma tanda (v2) | 1 | 46 passed; 1 aserción esperaba el índice nuevo, pero PostgreSQL rechazó primero por la unicidad de idempotencia existente; `stage4a-p2-targeted-v2.log` |
| Tanda anterior + `tests/test_assets.py tests/test_evidence_service.py tests/test_evidence_api.py` | 0 | **246 passed**; `stage4a-p2-targeted-final.log`, `P2_TARGETED_EXIT=0` |
| Ruff dirigido sobre app/tests/migración 08 | 0 | `stage4a-p2-ruff.log`; errores iniciales de longitud corregidos sin ignores |
| `pwsh -NoProfile -File audit-results/stage4a-p2-validate.ps1 -Phase storage` | 0 | **95 passed**, Docker `--network none --read-only`, tmpfs privado; `stage4a-p2-storage.log` |
| Wrapper `-Phase contracts` | 0 | Validadores actuales OpenAPI activos/evidencia, Ruff helper y `pip check`; runner sin red; `stage4a-p2-contracts.log` |
| Wrapper `-Phase https-default` → `scripts/authenticated-compose-smoke.ps1` | 0 | `stage4a-p2-https-default.log`; `AUTHENTICATED COMPOSE SMOKE: PASS` y `P2_https-default_EXIT=0` |
| Wrapper `-Phase https-evidence` → mismo script `-Evidence` | 0 | `stage4a-p2-https-evidence.log`; `AUTHENTICATED COMPOSE SMOKE: PASS` y `P2_https-evidence_EXIT=0` |
| Wrapper `-Phase exclusions` (primer intento) | 1 | Predicado de paquete PASS; inspección Compose sin env falló por POSTGRES_USER ausente; salida parcial `stage4a-p2-exclusions.log`. No fue aceptación. |
| Wrapper `-Phase exclusions`, inspección con `--env-file .env.example` | 0 | `stage4a-p2-exclusions-v2.log`: 27 rutas denegadas/4 fuentes, todos los artefactos audit-results excluidos; build contexts backend/frontend fuera de audit-results; sin ZIP |
| Wrapper `-Phase gate` → `scripts/backend-quality.ps1` (primera ejecución P2) | 1 | Backend: 607 passed, sentencias 99,35%, ramas 96,73%, Ruff y pip-audit PASS. El scanner final detectó solo el nombre de un log histórico; `stage4a-p2-gate.log`. No se presenta como gate aprobado. |

Los smokes comprueban sesión/cookies, CSRF/origins, RBAC/RLS y tenants. El default
deniega altas sin política incluso a platform_admin. Evidencia añade publicación
con LOGIN dedicado y cleanup de credencial, cinco operaciones, replay, no-store,
creación/edición/archivo de activos. El frontend sirve el proxy HTTPS; no se afirma
aceptación de una UI de evidencia. Son identidades técnicas, no aceptación humana.

El scanner rechazó `audit-results/stage4a-final-secret-scan.log` por su regla de
nombres, no por contenido secreto. Se inspeccionaron sus 173 bytes: aviso del
launcher Python, mensaje de escaneo aprobado y marcador histórico de exit 0.
Se renombró a `stage4a-final-pattern-check.log`, sin alterar bytes ni debilitar
el scanner. SHA-256 antes/después:
`CF39E07CDF19EB00B18A6BC55EE588CAC7B0AFB6CEA99A6CC8088FCC7A6A0DF4`.
Trazabilidad: `audit-results/stage4a-p2-log-rename.json`; la operación es reversible.
Se repitió el gate completo, sin sustituir su resultado fallido ni filtrar la
ruta denunciada. Los logs HTTPS históricos permanecen en sus nombres originales.

### Vinculación del código validado

Antes del gate y los smokes se registraron SHA-256 de **140** archivos pertinentes
(backend, pruebas, migraciones, scripts, Docker y configuración no secreta) en
`audit-results/stage4a-p2-source-hashes.json`. No incluye `.env` ni credenciales,
llaves, contenido cifrado o artefactos operativos. La comprobación final comparó
los 140 archivos con esos bytes: coincidencia completa, sin regenerar el manifiesto.

| Archivo | SHA-256 |
| --- | --- |
| Manifiesto `stage4a-p2-source-hashes.json` | `0829AA81404B6E3282812EB74E8F9CD25DCF83169136C48C1695E974A54352BD` |
| Migración 08 | `667A241A4B654310EC035A8B27DD19E1E0FD22693B4C7471E36B41365D88B091` |
| `backend/app/platform/database/models.py` | `B877229CF0B922003172D2A598F6EAF32C7C7046B93A00EA13E9E35D79A12850` |
| `backend/tests/test_policy_event_integrity.py` | `19B40C9E301CF057F3DBB56E59D16EDE2DF77BA70F6F8BF794F2763712DDEA39` |
| `backend/tests/test_policy_authority.py` | `857AF8B3D006659300A8684C31F61E9CCCD121E410B16E3A6B95827DA77F420C` |
| `backend/tests/test_policy_migration.py` | `F92D2600E21A29C28AC8DA01F140587E9ED1BF183CF9EB2E2BB175398ED41AA6` |

Además se ejecutó `docker image inspect ... --format '{{.RepoTags}} {{.Id}}'` y
`sha256sum` dentro de runners efímeros `--network none --read-only`, exit 0.
`stage4a-p2-image-hashes.log` confirma coincidencia de migración/modelo en runtime
y quality y de la prueba nueva en quality; ausencia de audit-results en los roots
de aplicación. Imágenes usadas (identificadores, no attestations de producción):

- Quality: `sha256:a20e7b06679a167ab6348de57fe5350c7e6a7c5f512092992f55c68a7fd894ee`.
- Backend: `sha256:5911aa806718d2cda9b738f2b91c40316210f0e6e9f474c9fc6ed1370dea0c0b`.

### Limpieza y alcance del diff

La tanda dirigida terminó antes de retirar `sentinelai-stage4a-p2`. Se consultó
PostgreSQL: **0** LOGINs `policy_test_*`, **0** asignaciones publicadoras y **0**
DB adicionales `closure_test_*`. Después `docker compose -p sentinelai-stage4a-p2
-f compose.quality.yaml down --remove-orphans` terminó con exit 0; registro
`stage4a-p2-dev-cleanup.log`. PostgreSQL usaba tmpfs, no volúmenes operativos.
Los smokes registran cleanup propio de contenedores/redes/volúmenes y el smoke de
evidencia registra eliminación del LOGIN/asignación. Las llaves/storage efímeros
desaparecen con su contenedor/tmpfs; no se afirma borrado seguro de memoria física.

Inventario del diff de entrega: **20 tracked modificados y 8 nuevos**, índice vacío.
Respecto al inventario histórico se añade `backend/app/platform/database/models.py`
y el nuevo `backend/tests/test_policy_event_integrity.py`; el resto de las rutas
se conserva. `git diff --stat` tracked: 251 inserciones/56 eliminaciones; los ocho
untracked no están incluidos en esa cifra. 01–07 y snapshots entregados permanecen
intactos. No hubo stage, commit, push, merge, paquete ni avance a 4B.

Las limitaciones de laboratorio de las secciones anteriores siguen vigentes:
`ownership_status=unverified`, sin decisiones humanas, escaneo, objetivos externos,
SENATI, autorizaciones completas, UI, workers ni entrega de outbox. La procedencia
declarada no acredita control humano ni autoriza escanear.

### Resultado definitivo recuperado tras la interrupción

Reanudación del 9 de octubre de 2026 (America/Lima): el segundo gate había terminado.
Se recuperó su log completo y se verificó el wrapper que captura el exit real del
proceso hijo; la antigua sesión de herramienta ya no estaba disponible. No se
reconstruyó el registro ni se volvió a ejecutar el gate. No había procesos activos
de gate/smokes/validación. El código coincide con el manifiesto anterior y los IDs
inmutables de las imágenes quality/runtime coinciden con los registrados arriba.

Comando terminado: `pwsh -NoProfile -File audit-results/stage4a-p2-validate.ps1
-Phase gate-v2`, que ejecutó `scripts/backend-quality.ps1 -NoBuild` sobre la imagen
reconstruida en la primera ejecución P2, sin cambios posteriores de código.
Log: `audit-results/stage4a-p2-gate-v2.log`, **exit 0** confirmado por
`P2_gate-v2_EXIT=0` después del escaneo y cleanup. Proyecto exclusivo:
`sentinelai-closure-quality-e380cd8bab`.

| Control definitivo | Exit | Resultado |
| --- | --- | --- |
| Alembic `upgrade head` | 0 | Instalación limpia 01–08 |
| Ruff `format --check` / `check` | 0 / 0 | 102 archivos conformes; sin findings |
| pytest completo con PostgreSQL real | 0 | **607 passed, 0 failed, 0 skipped**, 3 warnings conocidos |
| Cobertura de sentencias / ramas | 0 (gate) | **99,35% / 96,73%**, requisito de ramas >=90% intacto |
| `pip check` | 0 | No broken requirements found |
| `pip-audit --progress-spinner=off -r requirements.txt` | 0 | No known vulnerabilities found; versión 2.10.1 |
| Secret scan del repositorio | 0 (gate) | No high-confidence patterns detected; reglas sin cambios |
| Cleanup del proyecto del gate | 0 (gate) | Contenedor PostgreSQL y red retirados; tmpfs descartado |

El gate incluye las pruebas de migración 07 con datos →08, convergencia de esquema
y grants, downgrade seguro, restricciones SQL, rollback, concurrencia y commit
incierto. Cobertura Python: no equivale a cobertura de ramas del código PL/pgSQL;
las defensas SQL se acreditan mediante las regresiones específicas descritas arriba.
Los warnings son Starlette/httpx y runpy en bootstrap/onboarding, sin supresión.
Los 246 focalizados y los 95 de storage son ejecuciones complementarias, no se
suman a los 607 para inflar el total. Ambos smokes, OpenAPI y exclusiones se
recuperaron de sus logs completos sobre el mismo código, sin repetirlos.

Comprobación nueva al reanudar: `pwsh -NoProfile -File
audit-results/stage4a-p2-final-check.ps1`, **exit 0**. Registro:
`audit-results/stage4a-p2-final-check-v2.log`. Confirma rama/HEAD/origin local,
índice vacío, diff sin errores, 140 hashes coincidentes, 01–07/snapshots intactos,
marcadores de ambos smokes y ausencia de procesos/runners de aceptación.
Por etiquetas exactas, **0 contenedores, 0 redes y 0 volúmenes** en los diez proyectos:

- `sentinelai-stage4a-p2` y `sentinelai-stage4a-dev`.
- `sentinelai-closure-quality-7a5eaa8f06` y `sentinelai-closure-quality-e380cd8bab`.
- `sentinelai-smoke-49462bee4f2c0eb9` y `sentinelai-smoke-4a0a0f5d10bd4a36`.
- Históricos `sentinelai-closure-quality-71ef15f1d1`,
  `sentinelai-closure-quality-691dfef982`, `sentinelai-smoke-24002a40e2c91ea6`
  y `sentinelai-smoke-61be3557f38ce340`.

No se retiraron ni alteraron recursos operativos. Los seis runners nominados de
storage, contratos y contraste de hashes también están ausentes; imágenes/cachés
se conservan. `audit-results` sigue ignorado por Git, fuera de los contextos de
imagen backend/frontend y rechazado por el predicado de paquete. No se creó ZIP.

Después de recuperar el gate solo se finalizó este handoff y se generaron registros
locales de comprobación. Esos cambios documentales no se presentan como código
ejecutado por pytest; sus hashes se registran por separado en
`audit-results/stage4a-p2-document-hashes.json`. El manifiesto de 140 fuentes no se
regenera. El primer gate P2 conserva **exit 1**, y el resultado histórico de 578
pruebas permanece separado. El fallo de captura histórica P3 queda explícito,
no borrado por la aceptación nueva.

**Estado de entrega: remediación P2/P3 lista para reauditoría focalizada.**
Rama `feature/sprint-2a-assets-scope`, HEAD
`e5e13873dadc2ef79b6f208169115680a83fbcc1`; 20 modificados y 8 nuevos, índice vacío.
Sin stage, commit, push, merge, paquete ni avance a 4B. Esto no es aprobación
productiva, autorización de escaneo ni decisión humana de verificación.
