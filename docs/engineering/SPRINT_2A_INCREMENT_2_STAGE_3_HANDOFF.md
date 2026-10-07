# Etapa 3 — handoff

Estado: correcciones P2/P3 completadas; lista para reauditoría focalizada.
Actualización: 2026-10-07, America/Lima. La sección final contiene los resultados
posteriores a las correcciones; los resultados de 522 pruebas son históricos.
Checkpoint comprobado: feature/sprint-2a-assets-scope, HEAD y origin local
320e66dea0276df6330a77556bdc2e2088976ea1; árbol e índice inicialmente limpios.
Contrato previo: SPRINT_2A_INCREMENT_2_STAGE_3.md.

## Implementación y contrato

Cinco operaciones bajo /api/v1/evidence/assets/{asset_id}: POST presentación,
GET listado, GET /summary, GET /{evidence_id}, GET /{evidence_id}/content.
El recurso es la operación committed referenciada, nunca un expediente ficticio.
La IP procede exclusivamente del inventario. Documento cerrado de etapa 1, límite
streaming 16 KiB antes de acumular el body completo, sin compresión ni adjuntos.

El POST reutiliza el escritor/coordinador y sus transacciones breves. Se añade el
binding HTTP de 24h en http_idempotency_records durante la reserva: solo operation_id,
fingerprint y contexto actor/tenant/método/ruta/If-Match. No contiene plaintext ni
respuesta histórica sensible. Un replay reautoriza activo, versión y política;
pending/reclaimed/aborted no vuelven a escribir. El replay confirmado devuelve
metadatos actuales. Se conservan cuotas, estados y atomicidad DB de referencia,
auditoría/outbox; un commit incierto nunca elimina el objeto.

Lectura: autorización inicial en transacción corta, lectura/descifrado fuera de DB,
revalidación de identidad/permisos/retención y commit de evidence.content_read antes
de responder. Fallo de auditoría o de commit, incluso respuesta perdida tras commit,
impide devolver plaintext. Cache-Control: no-store y errores sanitizados en la
frontera HTTP evitan contenido/rutas/parámetros SQL en respuestas o exception logs.

Matriz: owner/security_manager presentan y leen todo su tenant; analyst presenta y
lee únicamente lo propio; auditor lee sin presentar; viewer/platform_admin solo
resumen mínimo. Permisos evidence:write, metadata, read, read_own y summary, sin
bypass platform:admin. Tenant siempre desde ActorContext y sesiones vigentes.

La revisión 07 se justifica únicamente por la nueva matriz de permisos. Agrega cuatro
permisos y evidence:write para analyst; downgrade revierte esos datos. No cambia
tablas, RLS forzado, FK compuestas, grants SQL ni migraciones 01–06. La prueba 07
compara schema/grants antes/después y verifica los pares rol/permiso exactos. La
prueba histórica de downgrade de evidencia ahora compara con la revisión de inicio
de su transacción, para demostrar rollback íntegro también desde 07.

Activos archivados/IP retirada conservan historia legible autorizada. A los 90 días
el contenido devuelve 410, los metadatos siguen disponibles y no se purga nada.
ownership_status sigue unverified; no hay aprobación, control efectivo o escaneo.

## Evidencia histórica anterior a las correcciones P2/P3

| Comando/comprobación | Exit | Resultado |
| --- | --- | --- |
| PostgreSQL focalizado: up y alembic upgrade head | 0 | Revisiones 01–07 |
| Ruff final de app/tests/07 y helper HTTPS | 0 | 87 archivos, formato y lint PASS |
| pytest focalizado API/contrato/migración 07 | 0 | 62 passed, 34,24 s |
| pytest dentro de backend-quality.ps1 | 0 | 522 passed, 160,25 s |
| backend-quality.ps1 completo y cleanup | 0 | PASS |
| pip-audit 2.10.1, requirements.txt | 0 | No known vulnerabilities found |
| Secret scan del gate | 0 | Sin patrones de alta confianza |
| Ruff format/check dentro del gate | 0 cada uno | 94 archivos, PASS |
| Cobertura global de sentencias | — | 2.319/2.330 = 99,53% |
| Cobertura global de ramas | — | 439/450 = 97,56% |
| Storage sin red | 0 | 95 passed; 357/357 sentencias y 66/66 ramas = 100% |
| OpenAPI generado frente a ambos snapshots | 0 | assets=5 y evidence=5 operaciones, MATCH |
| pip check e inspección de exclusiones en imagen | 0 | PASS |
| test-package-file-policy.ps1 | 0 | 24 exclusiones, 4 fuentes conservadas; sin ZIP |
| git check-ignore --no-index -v | 0 | 8 rutas de datos/llaves/locks excluidas |
| HTTPS -Evidence, incluye regresiones de activos/autenticación | 0 | PASS |
| HTTPS configuración por defecto | 0 | PASS |
| git diff --check | 0 | Sin errores de whitespace |

No se reducen los umbrales ni se atribuyen los gates históricos de 457/456/455 a
esta entrega. Las últimas ediciones posteriores al gate son documentación solamente.
Los tres warnings globales son la deprecación existente de Starlette/httpx y los
dos warnings runpy de bootstrap/onboarding. La ejecución focalizada inicial tuvo
9 fallos de fixtures (contexto SQL de cambio de tenant, suspensión masiva de la
membresía platform_admin protegida y archivado sin motivo); se corrigieron los
fixtures y se revalidó sin debilitar constraints. Hubo ajustes de Ruff antes de
la comprobación final; esos intentos intermedios no son gates satisfactorios.

Las 62 focalizadas incluyen RBAC, platform_admin con permisos adicionales, dos
tenants, lectura propia/ajena, CSRF/Origin, revocación de sesión/membresía/permisos
durante I/O, errores sanitizados, corrupción/llave ausente/storage inaccesible,
auditoría fallida y commit de lectura incierto, streaming sin Content-Length,
duplicados/extra, expiración/replay/conflicto, concurrencia y fases de commit incierto.
Se comprueba ausencia de transacciones runtime abiertas durante prepare/write/read
y se bloquea resolución de los objetivos sintéticos. Los módulos de etapa 1 no
se modificaron; sus 95 pruebas se repiten sin red.

## Comandos y registros

Desde C:\SentinelAI\platform; runners con backend montado en solo lectura salvo
formato/generación explícita del nuevo snapshot. No se cambia el snapshot de activos.

```powershell
docker compose -p sentinelai-evidence-stage3-check -f compose.quality.yaml up -d --wait postgres
docker compose -p sentinelai-evidence-stage3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace -e DATABASE_URL=postgresql+psycopg://sentinelai_migrator:ephemeral-test-only@postgres:5432/closure_test quality alembic upgrade head
docker compose -p sentinelai-evidence-stage3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace quality pytest -q --no-cov -p no:cacheprovider tests/test_evidence_api.py tests/test_evidence_api_migration.py tests/test_evidence_contract.py --tb=short
docker compose -p sentinelai-evidence-stage3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -v "${PWD}/scripts:/scripts:ro" -w /workspace quality sh -c "ruff format --check app tests alembic/versions/20261006_07_evidence_api_permissions.py /scripts/authenticated-compose-smoke-helper.py && ruff check app tests alembic/versions/20261006_07_evidence_api_permissions.py /scripts/authenticated-compose-smoke-helper.py"
.\scripts\backend-quality.ps1
docker run --rm --name sentinelai-evidence-stage3-storage --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 -e COVERAGE_FILE=/tmp/.coverage -v "${PWD}/backend:/workspace:ro" -w /workspace sentinelai-closure-quality:local pytest storage_tests -q -p no:cacheprovider -o "addopts=--strict-config --strict-markers --cov=app.evidence.contracts --cov=app.evidence.crypto --cov=app.evidence.filesystem --cov=app.evidence.configuration --cov=app.evidence.storage --cov-report=term-missing --cov-report=json:/tmp/coverage.json --cov-fail-under=90"
.\scripts\authenticated-compose-smoke.ps1 -Evidence
.\scripts\authenticated-compose-smoke.ps1
.\scripts\test-package-file-policy.ps1
git diff --check
docker compose -p sentinelai-evidence-stage3-check -f compose.quality.yaml down --remove-orphans
```

OpenAPI se compara mediante assets_contract(app.openapi(), csrf_header_name) y
evidence_contract() con los JSON de tests/contracts. El runner
sentinelai-evidence-stage3-contract usa --network none, --read-only, tmpfs /tmp,
ENVIRONMENT=test y ejecuta pip check; inspecciona la imagen sin mounts fuente,
exigiendo ausencia de .evidence, .kek, .evidence.lock y .evidence.coordinator.lock.

Logs locales ignorados en audit-results/ (no se genera paquete):
sprint-2a-stage3-focused-final.log, sprint-2a-stage3-ruff-final.log,
sprint-2a-stage3-quality.log, sprint-2a-stage3-storage.log,
sprint-2a-stage3-contract.log, sprint-2a-stage3-https-evidence.log,
sprint-2a-stage3-https-default.log, sprint-2a-stage3-focused-cleanup.log,
sprint-2a-stage3-runtime-exclusions.log y sprint-2a-stage3-cleanup.json.

## HTTPS y cleanup

El smoke -Evidence genera KEK aleatoria dentro del tmpfs del backend con modo 0600
y roots 0700 separados, exclusivamente en su proyecto. El helper requiere una DB
sentinelai_smoke_* y ENVIRONMENT=test. La prueba HTTPS verifica las cinco operaciones,
replay sin duplicados, CSRF, denegación platform_admin, no-store, tenant ajeno 404,
una referencia/operación/auditoría stored/outbox, un acceso auditado y cadena íntegra.
También ejecuta las regresiones previas de activos, sesiones, RLS, RBAC y logout.
El smoke por defecto prueba política vacía cerrada y configuración sin storage.

Proyectos exactos de esta ejecución:

- sentinelai-evidence-stage3-check: PostgreSQL tmpfs focalizado, ya retirado.
- sentinelai-closure-quality-788768c753: gate completo, retirado.
- sentinelai-smoke-82978366d831c3dd: HTTPS evidencia, retirado con sus volúmenes.
- sentinelai-smoke-56d7a8d92ec8a2a1: HTTPS por defecto, retirado con sus volúmenes.

Antes de retirar el PostgreSQL focalizado: test_roles=0, temporary_databases=0 y
operational_assignments=0. Los fixtures comprueban la eliminación de roots/llaves;
los runners son --rm. No se tocaron proyectos, volúmenes ni secretos operativos.
Los volúmenes DB/Redis de HTTPS son desechables; evidencia/llaves viven en tmpfs.
Verificación Docker final por etiqueta exacta: **0 contenedores, 0 redes y 0
volúmenes en los cuatro proyectos**, exit 0. También están ausentes los runners
sentinelai-evidence-stage3-storage, sentinelai-evidence-stage3-contract y
sentinelai-evidence-stage3-runtime-inspect. La inspección adicional de imagen runtime
y pip check terminó con exit 0, sin evidencia/llaves/locks en /app. Se conservan
las imágenes/cache normales de build. Una consulta auxiliar docker top devolvió 1
porque el runner del gate ya se había eliminado; no fue un fallo de validación.

## Inventario histórico previo a P2/P3 y límites

9 modificados: backend/app/api/v1/router.py, backend/app/evidence/service.py,
backend/app/main.py, backend/quality_gate.py, backend/tests/assets_contract.py,
backend/tests/test_evidence_migration.py, compose.yaml,
scripts/authenticated-compose-smoke-helper.py, scripts/authenticated-compose-smoke.ps1.

10 nuevos: backend/alembic/versions/20261006_07_evidence_api_permissions.py,
backend/app/api/v1/evidence.py, backend/app/evidence/access.py,
backend/app/evidence/schemas.py, backend/tests/contracts/evidence.openapi.json,
backend/tests/test_evidence_api.py, backend/tests/test_evidence_api_migration.py,
backend/tests/test_evidence_contract.py, esta guía y SPRINT_2A_INCREMENT_2_STAGE_3.md.

Misma rama/HEAD, índice vacío. Sin stage, commit, push, merge, paquete ni cambio de
rama. Migraciones 01–06, snapshots históricos y OpenAPI de activos intactos.

No se implementaron UI, workflow humano, aprobación/rechazo/revocación, autorización
de alcance, evaluate, scanner, workers, publisher, backups o rotación operacional.
No se contactaron objetivos; solo infraestructura efímera y servicios de dependencias
del gate. Linux local compatible con descriptores/nofollow/flock/hard links/fsync;
Windows nativo sigue fallando cerrado. Roots/KEK/volumen persistente operativos
requieren aprovisionamiento externo revisado. Las pruebas de recuperación y tmpfs
no demuestran durabilidad ante pérdida de energía ni atomicidad PostgreSQL/FS.
No se declara preparación productiva, revisión humana realizada ni permiso de escaneo.
Detener aquí la entrega. El siguiente paso es auditoría independiente del diff;
no continuar al workflow humano ni hacer commit automáticamente.

## Correcciones P2/P3 y cierre posterior — 2026-10-07

Precondiciones verificadas: rama feature/sprint-2a-assets-scope, HEAD
320e66dea0276df6330a77556bdc2e2088976ea1, 9 modificados + 10 nuevos, índice vacío.
Se conservaron todos los cambios de etapa 3, sin restauraciones ni cambio de rama.

### P2: raíz protegida completa y disposición explícita

La fábrica real de la API llama a evidence.layout.protected_root. El checkout
se identifica mediante la disposición backend/app/api/v1/evidence.py y los archivos
compose.yaml, frontend/package.json, scripts/backend-quality.ps1 y backend/alembic.ini.
Se protege toda su raíz, incluidos los directorios hermanos de backend. No depende
del cwd ni de .git, sea este ausente, directorio o archivo de worktree.

El Dockerfile crea /etc/sentinelai-application-root como root: contiene /workspace
en quality y /app en runtime. Debe coincidir con la raíz deducida de la disposición
de la API y existir alembic.ini. Disposiciones desconocidas fallan cerradas. Se
verificaron tanto el marcador real de la imagen como el checkout completo montado
en solo lectura; no se sustituyó parents[3] por otro índice sin comprobarlos.

La configuración valida ambas ubicaciones antes de abrir llaves o storage. La
validación de ubicación se comparte con PrivateDirectory; se mantienen el recorrido
por descriptores, O_NOFOLLOW, controles de permisos, escritura exclusiva y rechazo
de //, traversal y Windows nativo. resolve no reemplaza la apertura segura.

Las 30 regresiones nuevas de layout invocan get_access(...).storage_factory():
storage/llaves dentro de la raíz completa y de backend, raíz de imagen, externos
válidos, cwd diferente, .git ausente/archivo/directorio, disposición desconocida,
marcadores inválidos, //, igualdad/anidamiento ambiguos, traversal, symlinks y Windows.
Los rechazos léxicos interceptan os.open para exigir fallo antes de abrir llaves o
storage. La regresión adicional de API verifica la imagen real y una presentación.
La comprobación adicional contra /repository recorre la fábrica real con el
checkout montado, comprueba cuatro ubicaciones prohibidas y externos válidos.

### P3: cabeceras del contrato

OpenAPI declara Origin opcional exclusivamente en POST, con validación de origen
confiable cuando se proporciona y respuesta 403 si no coincide. Idempotency-Replayed
aparece exclusivamente en el 201 del POST, como string true/false. Cache-Control
declara no-store en éxitos y errores de las cinco operaciones. No se modificaron
los controles de autenticación, CSRF ni Origin para ajustar el esquema.

Snapshot de evidencia regenerado y prueba semántica ampliada: presencia, alcance,
obligatoriedad y valores de las cabeceras, además de igualdad del snapshot, cookie,
CSRF, If-Match, Idempotency-Key, límites y referencias. El snapshot de activos y
sus cinco operaciones permanecen intactos, igual que las migraciones 01–06.

### Resultados finales y cronología verificable

Los logs definitivos están en audit-results/stage3-fixes-final-*; el runner local
audit-results/stage3-fixes-validate.ps1 conserva los comandos y captura todos los
streams con *>&1 y Tee-Object, más marcador FINAL y exit status. No se incorporan
estos artefactos al Git ni a un paquete. Los logs contienen fechas UTC explícitas.

Hubo una primera validación de 553 pruebas con fixtures sintéticos que no detectó
el marcador incorrecto package.json. La comprobación del checkout real falló con
exit 1 (stage3-fixes-runtime.log). Se corrigió a frontend/package.json y se
repitieron las validaciones finales. Los logs stage3-fixes-* sin final conservan
los intentos intermedios; no sustentan el cierre. También hubo un ajuste de Ruff
por longitud de línea antes de las validaciones definitivas.

Al reanudar se recuperaron los marcadores completos del gate, ambos smokes y cleanup;
no se dedujo éxito por ausencia de procesos. La captura SHA256 previa de 220 archivos
coincidía íntegramente con el árbol al reanudar. No hubo cambios de backend, tests,
Dockerfile o snapshot posteriores a los resultados definitivos siguientes.

| Comando/fase del runner | Exit | Resultado posterior al marcador corregido |
| --- | --- | --- |
| prepare: Ruff y generación de snapshot | 0 | Formato/lint PASS; snapshot generado |
| focused: PostgreSQL, alembic upgrade head, pytest | 0 | 93 passed, 26,52 s; layout/API/contrato/migración 07 |
| checkout: fábrica API y checkout real montado :ro | 0 | Rechazos antes de os.open, externos válidos, fixtures eliminados |
| gate: scripts/backend-quality.ps1 | 0 | 553 passed, 130,06 s; FINAL quality EXIT=0 |
| Sentencias globales | — | 2345/2356 = 99,53% |
| Ramas globales | — | 447/458 = 97,60% |
| Ruff dentro del gate | 0 cada uno | 96 archivos, formato y lint PASS |
| pip-audit 2.10.1 dentro del gate | 0 | No known vulnerabilities found |
| Secret scan dentro del gate | 0 | Sin patrones de alta confianza |
| storage: pytest storage_tests con --network none | 0 | 95 passed, 1,30 s; 361/361 sentencias y 66/66 ramas: 100% |
| contract: OpenAPI generado y pip check | 0 | MATCH, activos=5 y evidencia=5; sin dependencias rotas |
| runtime: layout real, pip check y exclusiones | 0 | /app y /repository correctos, marcador root, imágenes sin datos/llaves/locks |
| https-evidence: authenticated-compose-smoke.ps1 -Evidence | 0 | AUTHENTICATED COMPOSE SMOKE: PASS; FINAL https-evidence EXIT=0 |
| https-default: authenticated-compose-smoke.ps1 | 0 | AUTHENTICATED COMPOSE SMOKE: PASS; FINAL https-default EXIT=0 |
| exclusions: política de paquete y git check-ignore | 0 | Sin ZIP; datos/llaves/locks excluidos |
| cleanup: consulta SQL y retirada del proyecto focalizado | 0 | Roles de test=0, DB temporales=0, asignaciones=0; FINAL focused-cleanup EXIT=0 |

Las 522 pruebas previas no se atribuyen a estas correcciones. El aumento a 553 son
30 regresiones de layout y una de integración con la imagen real. Los tres warnings
del gate siguen siendo los de Starlette/httpx y runpy de bootstrap/onboarding.
No se redujeron umbrales. Las coberturas de storage no incluyen layout; este módulo
sí se mide en el gate global (20/20 sentencias y 8/8 ramas).

Comandos de reproducción, desde C:\SentinelAI\platform (no se repitieron al reanudar):

```powershell
.\audit-results\stage3-fixes-validate.ps1 -Phase prepare
.\audit-results\stage3-fixes-validate.ps1 -Phase focused
.\audit-results\stage3-fixes-validate.ps1 -Phase checkout
.\audit-results\stage3-fixes-validate.ps1 -Phase gate
.\audit-results\stage3-fixes-validate.ps1 -Phase storage
.\audit-results\stage3-fixes-validate.ps1 -Phase contract
.\audit-results\stage3-fixes-validate.ps1 -Phase runtime
.\audit-results\stage3-fixes-validate.ps1 -Phase https-evidence
.\audit-results\stage3-fixes-validate.ps1 -Phase https-default
.\audit-results\stage3-fixes-validate.ps1 -Phase exclusions
.\audit-results\stage3-fixes-validate.ps1 -Phase cleanup
```

Después de recuperar esos resultados se detectó que el filtro de paquete excluía
.log, pero no todo audit-results. Se añadió únicamente ese directorio a
scripts/package-file-policy.ps1 y tres regresiones JSON/PS1/TXT en
scripts/test-package-file-policy.ps1. La comprobación pertinente posterior pasó:
27 exclusiones y 4 fuentes conservadas, exit 0, sin generar ZIP; registro
stage3-fixes-final-audit-exclusions.log. No afecta backend ni despliegue, por lo que
no se repitieron el gate ni los smokes. Git ignora el directorio completo; está fuera
de los contextos Docker ./backend y ./frontend. El cierre repite git diff --check.

### Cleanup confirmado y estado final

La consulta posterior a la interrupción confirmó cero procesos de validación activos,
cero contenedores, redes y volúmenes para cada proyecto exacto, incluidos intermedios:

- sentinelai-stage3-fixes
- sentinelai-closure-quality-e82b3bb0a6 (intermedio)
- sentinelai-closure-quality-421ef47a96 (final)
- sentinelai-smoke-2dbc8ec25340df24 (evidencia intermedio)
- sentinelai-smoke-f8d3358568109d47 (default intermedio)
- sentinelai-smoke-046649bdaba95c8e (evidencia final)
- sentinelai-smoke-ce8e858dc225cd00 (default final)

Ausentes los runners exactos sentinelai-stage3-fixes-storage,
sentinelai-stage3-fixes-contract y sentinelai-stage3-fixes-runtime. Registro:
audit-results/stage3-fixes-final-cleanup.json, consulta exit 0. No se necesitó retirar
recursos al reanudar: el cleanup ya había terminado. Los fixtures comprueban la
eliminación de roots y KEK, y los tmpfs desaparecen con sus contenedores. No se
inspeccionaron ni eliminaron datos, llaves o volúmenes operativos; se conservan
imágenes y cachés normales de build.

Archivos tocados por las correcciones respecto al árbol auditado: backend/Dockerfile;
backend/app/api/v1/evidence.py; backend/app/evidence/{configuration,filesystem,layout}.py;
backend/tests/{test_evidence_api,test_evidence_contract,test_evidence_layout}.py;
backend/tests/contracts/evidence.openapi.json; esta guía y SPRINT_2A_INCREMENT_2_STAGE_3.md;
scripts/package-file-policy.ps1 y scripts/test-package-file-policy.ps1.

Inventario final acumulado de etapa 3: 14 archivos modificados + 12 nuevos, índice
vacío, misma rama y HEAD. El inventario histórico de 9+10 se conserva arriba para
trazabilidad. Sin stage, commit, push, merge, paquete ni avance al workflow humano.
Estado listo para reauditoría focalizada, no preparación productiva ni permiso de
escaneo. Windows nativo sigue rechazado; recuperación en tmpfs no demuestra
durabilidad ante pérdida de energía de un volumen persistente.
