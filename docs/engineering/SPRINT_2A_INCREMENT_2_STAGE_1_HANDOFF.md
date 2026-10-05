# Handoff — Sprint 2A incremento 2, etapa 1

Fecha de implementación: 2026-09-30 (America/Lima).
Última revalidación: 2026-10-02 (America/Lima).
Estado: P2 CORREGIDO — PENDIENTE DE REAUDITORÍA FOCALIZADA.
No es cierre del incremento 2, Sprint 2A, workflow humano ni aprobación productiva.

## Corrección P2 posterior a la auditoría, 2 de octubre

La auditoría identificó que pathlib conserva la raíz POSIX `//`, mientras el
adaptador recorre componentes desde `/`. La comparación léxica podía aceptar
datos y llaves en la misma ubicación, anidados o dentro del checkout.

Se añade `reject_ambiguous_posix_root` en filesystem.py. Configuración lo aplica
a storage y llaves antes de comparar ubicaciones. PrivateDirectory lo aplica
también a su raíz y a repository_root antes de resolve, comparaciones o aperturas.
Así quedan cubiertos configured_storage y usos directos de PrivateDirectory,
LocalEvidenceStorage y FileKeyProvider. La corrección rechaza la raíz ambigua;
no sustituye O_NOFOLLOW/O_DIRECTORY ni el recorrido por descriptores por resolve.
El rechazo de Windows nativo sigue precediendo la apertura del almacenamiento.

Archivos editados únicamente para este P2:
- backend/app/evidence/filesystem.py
- backend/app/evidence/configuration.py
- backend/storage_tests/test_local_storage.py
- docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_1_HANDOFF.md

Se conservó todo el diff previo. Checkpoint inicial confirmado: rama
feature/sprint-2a-assets-scope, HEAD 17f63934495f259b0ea701df2a951853fab791ed,
9 tracked modificados + 15 nuevos e índice vacío. No hay endpoints, migraciones,
paquetes, nuevas etapas ni cambios en las pruebas o controles del incremento 1.

Regresiones reales del módulo: igualdad `/` frente a `//`, anidamiento en ambos
sentidos y con cualquiera de las raíces ambigua, rutas dentro del checkout,
ambas raíces ambiguas, checkout ambiguo y entrada directa por los tres adaptadores.
Las pruebas sustituyen os.open por una función que falla si se invoca; para el
checkout y el adaptador directo también interceptan resolve. No basta con que
falle por directorio o KEK ausente: debe emitir el error de raíz ambigua antes
de I/O. Se mantiene y amplía el roundtrip de configuración POSIX ordinaria.

Resultados posteriores al cambio de código, no heredados:

| Comando / comprobación | Exit | Resultado |
| --- | --- | --- |
| ruff format --check app/evidence storage_tests | 0 | 10 archivos ya formateados |
| ruff check app/evidence storage_tests | 0 | Sin hallazgos |
| pytest storage_tests/test_local_storage.py -q -p no:cacheprovider -k 'configuration or ambiguous or unsafe_directories or descriptor_pinning' -o 'addopts=--strict-config --strict-markers' | 0 | 16 passed, 17 deselected |
| pytest storage_tests sin red, con cobertura de app.evidence | 0 | 95 passed; 363/363 sentencias y 66/66 ramas, ambas 100% |
| pytest del gate completo | 0 | 409 passed, 3 warnings, 65.76 s |
| Cobertura global | — | Sentencias 1859/1865 = 99.68%; ramas 342/348 = 98.28%; combinada 99.46% |
| Ruff del gate completo | 0 cada comando | 81 archivos; format --check y check PASS |
| Rechazo Windows nativo con py -B | 0 | UnsafeStorage antes de abrir almacenamiento |
| .\scripts\backend-quality.ps1 | 0 | Gate completo y teardown final PASS |
| pip-audit --version / pip-audit --progress-spinner=off -r requirements.txt | 0 cada uno | 2.10.1; sin vulnerabilidades conocidas |
| Secret scan del gate | 0 | Sin patrones de alta confianza detectados |
| py -B scripts/secret_scan.py . tras actualizar el handoff | 0 | PASS |
| git diff --check | 0 | Sin errores; avisos LF/CRLF informativos |

Gate posterior a la corrección: inicio 20:12 UTC (15:12 Lima), proyecto
sentinelai-closure-quality-cd6a4bbc98. Las 13 regresiones adicionales elevan las
pruebas de storage de 82 a 95 y las globales de 396 a 409. Los tres warnings
anteriores de Starlette/httpx y runpy se mantienen visibles. La suite global
incluye la comparación OpenAPI y las pruebas del incremento 1 sin modificarlas.

Cleanup exacto comprobado por label del proyecto y nombres de los runners (exit 0):
0 contenedores, 0 redes, 0 volúmenes y 0 runners restantes. Cada fixture comprueba
la eliminación de su TemporaryDirectory. Se conserva la imagen/cache de build;
no se tocó storage operativo, secretos reales ni recursos ajenos.

Estado Git final: misma rama y HEAD, 9 modificados + 15 nuevos, índice vacío.
Comparación SHA-256 de los 24 archivos del diff: solo cambian los cuatro enumerados
arriba; los otros 20 conservan sus bytes. Sin stage, commit, push, merge ni paquete. Se deja el P2 para
reauditoría focalizada; no se inicia la siguiente etapa.

Comandos de los runners, desde la raíz del repositorio:

```powershell
docker run --rm --name sentinelai-evidence-p2-focused --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 -e RUFF_CACHE_DIR=/tmp/ruff -v "${PWD}/backend:/workspace:ro" sentinelai-closure-quality:local sh -c 'ruff format --check app/evidence storage_tests && ruff check app/evidence storage_tests && pytest storage_tests/test_local_storage.py -q -p no:cacheprovider -k "configuration or ambiguous or unsafe_directories or descriptor_pinning" -o "addopts=--strict-config --strict-markers"'
docker run --rm --name sentinelai-evidence-p2-storage --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 -e COVERAGE_FILE=/tmp/.coverage -v "${PWD}/backend:/workspace:ro" sentinelai-closure-quality:local pytest storage_tests -q -p no:cacheprovider -o "addopts=--strict-config --strict-markers --cov=app.evidence --cov-report=term-missing --cov-fail-under=90"
.\scripts\backend-quality.ps1
```

El primer intento del runner focalizado no accedió al pipe Docker del sandbox
(exit 1, sin ejecutar pruebas); el reintento autorizado pasó con exit 0. No se
relajaron reglas ni umbrales. Un comando auxiliar inicial de captura de hashes
tuvo un error de sintaxis PowerShell sin modificar archivos; se repitió con éxito.

Estas pruebas usan Linux Python 3.12.12 y fixtures tmpfs efímeras. Prueban rechazo,
semántica de escritura/recuperación y fallos de proceso; NO demuestran durabilidad
de un volumen persistente ante pérdida de energía. Las 62 pruebas portables Windows
anteriores no se repitieron: solo se comprobó de nuevo el rechazo nativo del adaptador.

## Reanudación del 2 de octubre anterior a la corrección P2

Se retomó por instrucción expresa del usuario conservando el diff existente.
Estado inicial: rama feature/sprint-2a-assets-scope; HEAD y referencia local
origin/feature/sprint-2a-assets-scope en
17f63934495f259b0ea701df2a951853fab791ed; 9 archivos tracked modificados y
15 nuevos, índice vacío. El árbol NO estaba limpio en esta reanudación; se informó
la diferencia y el usuario autorizó continuar. No se repitió git ls-remote.

Se revisaron los siete archivos app/evidence, las pruebas de storage, el diff
tracked, ADR especializado, contrato y este handoff. No fue necesario cambiar
código, configuración ni tests durante la reanudación; solo se actualiza este
handoff. Los resultados siguientes se obtuvieron de nuevo sobre ese código:

| Comando / ejecución actual | Exit | Resultado |
| --- | --- | --- |
| .\scripts\backend-quality.ps1 | 0 | Build, migraciones 01→05, Ruff, pytest, pip-audit, secret scan y cleanup |
| ruff format --check / ruff check, dentro del gate | 0 cada uno | 81 archivos ya formateados; sin hallazgos |
| pytest del gate | 0 | 396 passed, 3 warnings, 65.50 s |
| Cobertura del gate | — | Sentencias 1852/1858 = 99.68%; ramas 340/346 = 98.27%; combinada 99.46% |
| pytest storage_tests en contenedor --network none | 0 | 82 passed; sentencias 356/356 y ramas 64/64 = 100% |
| Comparación explícita assets_contract(app.openapi(), csrf_header_name) con tests/contracts/assets.openapi.json | 0 | OPENAPI MATCH operations=5 |
| python -m pip check, en el contenedor | 0 | No broken requirements found |
| pip-audit --version / pip-audit --progress-spinner=off -r requirements.txt | 0 cada uno | 2.10.1; sin vulnerabilidades conocidas |
| Secret scan del gate | 0 | Sin patrones de alta confianza detectados |
| py scripts/secret_scan.py . después de actualizar el handoff | 0 | PASS |
| .\scripts\test-package-file-policy.ps1 | 0 | 23 exclusiones y 4 rutas fuente; no genera ZIP |
| git check-ignore --no-index con cinco rutas sintéticas | 0 | Datos, llaves, ciphertext y temporales excluidos; sin crear archivos |
| git diff --check | 0 | Sin errores; avisos LF/CRLF informativos |

Gate iniciado a las 19:54 UTC (14:54 Lima), proyecto
sentinelai-closure-quality-63b6acf379. Se conservan los tres warnings históricos
de Starlette/httpx y runpy, sin silenciarlos. El primer intento del runner dirigido
falló por acceso al pipe Docker del sandbox (exit 1, no ejecutó pruebas); el
reintento autorizado terminó con exit 0. No se debilitó ninguna prueba o umbral.

Comando dirigido actual (desde la raíz del repositorio):

```powershell
docker run --rm --name sentinelai-evidence-stage1-resume-tests --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 -e COVERAGE_FILE=/tmp/.coverage -v "${PWD}/backend:/workspace:ro" sentinelai-closure-quality:local pytest storage_tests -q -p no:cacheprovider -o "addopts=--strict-config --strict-markers --cov=app.evidence --cov-report=term-missing --cov-fail-under=90"
```

La comparación OpenAPI y pip check usaron otro contenedor --rm, --network none,
--read-only: sentinelai-evidence-stage1-contract-check, con ENVIRONMENT=test,
tmpfs /tmp y backend montado en solo lectura. No se escribió el snapshot.

Cleanup verificado mediante consultas Docker por label exacta de proyecto y
nombres de los dos runners (exit 0): **0 contenedores, 0 redes, 0 volúmenes,
0 runners restantes**. Las fixtures verificaron también la desaparición de cada
TemporaryDirectory. No se aprovisionaron llaves ni storage operativos; se retiene
la imagen/cache de build. No se eliminaron recursos ajenos.

Se reconsultaron las referencias oficiales de cryptography enlazadas en el ADR:
las páginas stable se identifican como 50.0.2 y documentan AESGCM, nonce de 96 bits,
tag de 128 bits y AES-KW RFC 3394. Las rutas web versionadas /en/50.0.2/ no fueron
accesibles con la herramienta; se utilizó la documentación stable oficial y se
comprobó la compatibilidad instalada mediante el gate y pip check.

Las 62 pruebas Windows nativas corresponden al registro del 30 de septiembre;
NO se volvieron a ejecutar ni se corroboraron sus logs originales en esta
reanudación. El rechazo del adaptador fuera de Linux sí se volvió a probar por
simulación de plataforma dentro de la suite Linux. Tampoco se repitieron smokes
HTTPS: no hubo cambios nuevos de ejecución o contrato servido al retomar.
No se afirma durabilidad física ante corte de energía, restauración de backups,
rotación operacional ni atomicidad PostgreSQL/filesystem.

Estado final comprobado para revisión: misma rama/HEAD, 9 modificados y 15 nuevos,
índice vacío. El inventario completo de archivos figura más abajo; el único
archivo editado durante esta reanudación es este handoff. Detener aquí la etapa 1.

## Checkpoint y restricciones respetadas

Registro de la implementación del 30 de septiembre (anterior a la reanudación):

Repositorio C:\SentinelAI\platform, rama feature/sprint-2a-assets-scope.
HEAD inicial: 17f63934495f259b0ea701df2a951853fab791ed. Rama, HEAD, referencia
origin/feature/sprint-2a-assets-scope, árbol e índice limpio comprobados antes de
editar. git ls-remote confirmó el mismo commit remoto. Se mantuvo rama y HEAD.
No stage, commit, push, merge, nueva rama, ZIP ni edición de secretos existentes.

No cambiaron endpoints, contratos de activos, permisos, migraciones 01–05,
snapshots, tests anteriores, RLS/RBAC/CSRF, audit/outbox o idempotencia existente.
Se añade únicamente una dependencia productiva fijada: cryptography==50.0.2.

## Entrega

- Documento estricto lab_control_attestation_v1 (16 KiB), sin contenido arbitrario,
  IP alternativa, URLs, comandos o campos de autoridad; JSON canónico y validación
  de ingreso/historia separadas.
- Envelope con DEK aleatoria por objeto, AES-256-GCM, AAD ligado a contexto/objeto/
  key_id/wrapper, AES-KW con KEK externa y formato versionado.
- Puertos internos y adaptador Linux de directorio privado: apertura relativa a
  descriptores, no symlinks, archivos privados, publicación exclusiva y sync.
- Recibo interno preparado antes de I/O; lectura validada; recuperación de link
  temporal redundante; aborto explícito de temporal conocido. Nunca promoción o
  eliminación automática de un huérfano por edad.
- Sin plaintext de evidencia o material de llaves en disco, salvo KEK preaprovisionada
  en su directorio separado. La cabecera de metadatos no secretos va en claro.
- Exclusiones Git/build/paquete, tests separados sin dependencia DB pero integrados
  en el gate completo, ADR especializado y runbook.

Diseño aprobado y cambios respecto del plan:
[ADR-009 laboratorio](ADR_009_LAB_STORAGE.md).
Formato y garantías: [contrato/operación](SPRINT_2A_INCREMENT_2_STAGE_1.md).

## Resultados registrados el 30 de septiembre (ejecución anterior)

Gate completo ejecutado 2026-09-30, aproximadamente 18:29–18:31 Lima
(23:29–23:31 UTC), proyecto sentinelai-closure-quality-8ece7a3096.

| Métrica | Resultado |
| --- | --- |
| Pruebas completas | 396 passed, 0 failed, 0 skipped |
| Sentencias | 1852/1858 = 99.68% |
| Ramas | 340/346 = 98.27% |
| Umbral ramas | >=90%, sin cambios |
| Combinada pytest-cov | 99.46%, no confundir con sentencias |
| Storage dirigido Linux | 82 passed; 356/356 sentencias, 64/64 ramas |
| Contrato/crypto Windows nativo | 62 passed, 0 failed, 0 skipped |
| Migración limpia y suite existente de convergencia | PASS, hasta 05 sin nueva revisión |
| Ruff | PASS, 81 archivos formateados correctamente |
| pip-audit | 2.10.1; no known vulnerabilities found |
| Secret scan | PASS |

La suite incluye 314 pruebas existentes en el checkpoint y 82 nuevas. Las 62
portables son un subconjunto ejecutado además en Windows, no se suman a 396.
Tres warnings no silenciados: deprecación Starlette/httpx y dos runpy de bootstrap/
onboarding. No se cambió ninguna prueba ni umbral para obtener PASS.

## Comandos y exit status

| Comando / ejecución | Exit | Resultado |
| --- | --- | --- |
| git branch --show-current; git rev-parse HEAD; git rev-parse origin/feature/sprint-2a-assets-scope | 0 cada comando | Checkpoint esperado |
| git status --porcelain=v1; git diff --exit-code; git diff --cached --exit-code (inicio) | 0 cada comando | Árbol e índice limpios |
| git ls-remote --exit-code origin refs/heads/feature/sprint-2a-assets-scope | 0 | Remoto confirmado |
| docker compose -f compose.quality.yaml build quality | 0 | Python 3.12.12 Linux y dependencia compatible |
| ruff check app/evidence storage_tests inicial, dentro de Docker | 1 | 20 hallazgos de formato/imports/with, corregidos; no reglas relajadas |
| ruff format app/evidence storage_tests quality_gate.py dentro de Docker | 0 | Formato aplicado a archivos de esta etapa |
| ruff check app/evidence storage_tests quality_gate.py repetido | 0 | All checks passed |
| pytest storage_tests dirigido en Docker --network none (primero) | 0 | 79 passed |
| pytest storage_tests dirigido en Docker --network none (final, con crash/no-network) | 0 | 82 passed, cobertura del módulo 100% |
| py -m venv <directorio efímero>; pip install cryptography==50.0.2 pydantic==2.13.5 pytest==9.1.1 | 0 | Entorno Windows aislado, sin cambiar Python global |
| python -m pytest storage_tests/test_contract_crypto.py -q -p no:cacheprovider -o 'addopts=--strict-config --strict-markers' en ese venv | 0 | 62 passed, Python 3.14.7 Windows |
| .\scripts\backend-quality.ps1 | 0 | Gate completo y cleanup PASS |
| alembic upgrade head dentro del gate | 0 | 01→05, base PostgreSQL efímera |
| ruff format --check / ruff check sobre rutas del gate | 0 cada comando | Incluye storage_tests |
| pytest -q -p no:cacheprovider --cov-report=json:/tmp/coverage.json | 0 | 396 passed, 67.54 s |
| pip-audit --version; pip-audit --progress-spinner=off -r requirements.txt | 0 cada comando | 2.10.1, sin vulnerabilidades conocidas |
| python /repository/scripts/secret_scan.py /repository dentro del gate | 0 | PASS |
| .\scripts\test-package-file-policy.ps1 | 0 | 23 exclusiones + 4 rutas fuente; ningún ZIP |
| git check-ignore --no-index con rutas sintéticas de evidencia/KEK/temporales | 0 | Cinco rutas excluidas, sin crearlas |
| git diff --check | 0 | Sin errores de whitespace; avisos LF/CRLF no son fallos |
| py scripts\secret_scan.py . | 0 | PASS después de la documentación |
| git diff --name-only <checkpoint> -- backend/alembic backend/tests backend/app/api backend/app/assets backend/app/authorization backend/app/security_audit backend/app/platform/database backend/app/platform/outbox.py backend/app/platform/http_idempotency.py | 0, vacío | Foundation, activos y migraciones intactos |

Comando dirigido Linux final: docker run --rm --name sentinelai-evidence-stage1-tests
--network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001
-e COVERAGE_FILE=/tmp/.coverage -v <backend>:/workspace:ro
sentinelai-closure-quality:local pytest storage_tests -q -p no:cacheprovider
-o 'addopts=--strict-config --strict-markers --cov=app.evidence --cov-report=term-missing --cov-fail-under=90'.

La ejecución dirigida selecciona solo el módulo nuevo; NO sustituye el gate
global, que conserva pytest-cov y comprobación separada de ramas >=90%.

## Fallos y casos negativos comprobados

Documento extra/duplicado/sobredimensionado/no UTF-8/tipos ambiguos, fecha fuera de
ventana; campos IP/tenant/asset/secretos/URL/comando rechazados. Contexto cruzado en
los cuatro componentes, UUID de objeto distinto, llave ausente/equivocada/corta,
key_id incluso alias de la misma KEK, nonce/wrapper/ciphertext truncado o manipulado,
base64 y plaintext no canónicos. Ninguno devuelve plaintext.

Permisos abiertos, symlinks en raíces/ancestros/archivos, traversal, FIFO, hard links
de KEK y recibos con nombre forjado rechazados. Directorio renombrado no redirige
I/O a otra ruta. Cuatro escritores simultáneos: un éxito y tres conflictos.

Fallos antes de escritura, durante escritura parcial, fsync archivo, fsync de
directorio en cada fase, publicación y recuperación. Dos pruebas terminan realmente
el proceso hijo con os._exit durante escritura o después de publicación: se libera
flock y la recuperación conserva el final válido. No son ensayos de corte de energía.
Sockets/DNS Python interceptados durante prepare/write/read/recover: ningún intento.

## Cleanup verificado

- Proyecto sentinelai-closure-quality-8ece7a3096: 0 contenedores, 0 redes, 0 volúmenes.
- Runner sentinelai-evidence-stage1-tests: ausente; --rm y tmpfs, sin volumen persistente.
- Cada fixture usa TemporaryDirectory y verifica que su raíz ya no existe al salir.
- Venv Windows sentinelai-stage1-venv-c57e6c962f674eef9088288b787a8f78 eliminado de
  C:\Users\Victus\AppData\Local\Temp tras validar ruta absoluta, padre y ausencia
  de reparse point. Sin modificación de Python global.
- No se retiraron volúmenes/proyectos operativos. Se retienen imagen/cache de build.

## Archivos para revisar

Tracked modificados (9): .env.example, .gitignore, ROADMAP.md, backend/.dockerignore,
backend/Dockerfile, backend/pyproject.toml, backend/quality_gate.py,
backend/requirements.txt, scripts/package-closure.ps1.

Nuevos (15):
- backend/app/evidence/__init__.py
- backend/app/evidence/errors.py
- backend/app/evidence/contracts.py
- backend/app/evidence/crypto.py
- backend/app/evidence/filesystem.py
- backend/app/evidence/configuration.py
- backend/app/evidence/storage.py
- backend/storage_tests/conftest.py
- backend/storage_tests/test_contract_crypto.py
- backend/storage_tests/test_local_storage.py
- scripts/package-file-policy.ps1
- scripts/test-package-file-policy.ps1
- docs/engineering/ADR_009_LAB_STORAGE.md
- docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_1.md
- docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_1_HANDOFF.md

git diff --stat tracked: 9 archivos, 44 inserciones y 12 eliminaciones; NO incluye
los 15 nuevos porque no se hizo staging. Revisar también los untracked.

## Pendiente y protocolo de continuación

Detenerse para revisión humana. No iniciar automáticamente la etapa siguiente.
Al retomar, comprobar rama/HEAD/status/diff y recursos, conservar este trabajo y
no asumir gates completados que no consten aquí o en nuevos registros.

Pendientes: coordinación PostgreSQL/filesystem, referencias tenant y reservas,
idempotencia de mutaciones nuevas, audit/outbox del workflow, cuotas, retención/
purga/tombstones, backups/restore, rotación coordinada, custodia operacional,
API/CSRF y permisos de evidencia, separación de funciones, revisión humana y
revocación persistente por retirada de política. No hay estados APPROVED ni nueva
migración/endpoint. La selección de otra KEK no es rotación operacional completa.

Windows nativo NO soporta storage; se probó rechazo seguro, contrato y crypto.
Linux tmpfs y muerte de procesos NO prueban durabilidad física ante corte de energía.
No se ejecutó un nuevo smoke HTTPS: no hay rutas o workflow nuevo que aceptar;
la aceptación HTTPS del futuro flujo pertenece a su etapa de integración.
No hay custodio inventado, personas que hayan revisado evidencia, backups existentes,
autorizaciones, contactos de objetivos, UI, workers, publisher o scanner.
