# Sprint 2A, incremento 2, etapa 2 — handoff

Fecha de continuación: 2026-10-06 (America/Lima).
Estado: etapa 2 implementada y validada; lista para auditoría independiente.

## Checkpoint y alcance

- Repositorio: C:\SentinelAI\platform.
- Rama: feature/sprint-2a-assets-scope.
- HEAD de inicio y continuación: 894268192b1b5de96b010eed6216b82483d7b6fa.
- Inicio original: árbol e índice limpios. Reanudación: 6 modificados y 7 nuevos,
  verificados con git status --short --untracked-files=all; índice vacío.
- Implementación interna solamente. Sin endpoints, workflow humano, aprobaciones,
  conexiones/DNS hacia objetivos, scanners, backups, publisher ni paquetes.
- Migraciones 01–05, snapshots históricos, autenticación y contrato HTTP conservados.
  La revisión 06 no entregada incorpora las dos garantías finales.

## Implementado

Ver SPRINT_2A_INCREMENT_2_STAGE_2.md para invariantes, locks y uso operacional.
El servicio reutiliza la autenticación vigente en cada transacción; no conserva
autoridad tras I/O. Reserva -> cifrado -> recibo prepared -> publicación exclusiva
-> revalidación -> confirmación de referencia/cuota/auditoría/outbox en un commit.

Estados: reserved -> prepared -> committed; recuperación
reserved/prepared -> reclaimed -> aborted. La reserva no aparece como versión
confirmada y el reconciliador nunca promueve archivos. Un resultado incierto de
commit conserva objeto y reserva; replay no repite mutaciones ni eventos.

El coordinador toma flock exclusivo no bloqueante antes de DB y lo mantiene durante
las fases. Cada transacción usa organización -> activo (escritor) -> cuota ->
operación -> audit lock (confirmación). No hay filesystem ni cifrado bajo locks DB.
La recuperación confirma primero reclaimed, limpia fuera de DB y luego libera cuota.
Se conservan objetos referenciados, desconocidos y finales corruptos.

La revisión 06 crea cuatro tablas con RLS forzado: operaciones, referencias,
cuotas y asignaciones de tenants a credenciales operacionales. Incluye FK compuestas,
transiciones controladas, referencias inmutables y grants por columna. Runtime no
administra asignaciones, límites ni referencias existentes. Mantenimiento solo lee
tenants provisionados y puede reclamar/abortar; no confirma ni escribe referencias.

### Correcciones finales

1. Después de un unlink con fsync fallido, el siguiente intento sincroniza el
   directorio aunque ya no encuentre el archivo. Fallo antes de sincronizar o
   respuesta perdida después de sincronizar mantienen reclaimed y cuota reservada.
   La prueba repite el fallo con el archivo ausente y luego demuestra recuperación.
2. evidence_maintenance_tenants limita el LOGIN a tenants provisionados. El servicio
   y RLS comprueban la asignación; SQL directo con otro app.organization_id no ve ni
   modifica operaciones/cuotas/referencias. No puede autoaprovisionarse. Un trigger
   impide que mantenimiento pase prepared a committed incluso mediante SQL directo.

## Procedencia de resultados

- Gate previo al corte: 455 pruebas, sentencias 99,67%, ramas 98,26%, Ruff,
  pip-audit y secret scan PASS. Log local: audit-results/sprint-2a-stage2-quality.log.
- El comando que quedó en ejecución terminó con 456 pruebas, las mismas coberturas
  redondeadas y cleanup del proyecto sentinelai-closure-quality-f67a7f4669.
  Log: audit-results/sprint-2a-stage2-quality-final.log. Incluía la primera regresión
  de fsync, pero la imagen precedía las ediciones de asignaciones operacionales.
  Ruff format --check y ruff check terminaron ambos con exit 1 (formato y E501).
  Las 456 pruebas pasaron, pero ese gate intermedio no fue satisfactorio.
  **No es validación del estado final con tenants provisionados.**
- Pruebas focalizadas, gate completo y verificaciones posteriores a las últimas
  ediciones de código/pruebas se registran debajo. Solo la documentación de cierre
  se completó después del gate. Logs locales excluidos de Git,
  disponibles para auditoría en esta máquina; no se genera un ZIP.

## Validación final de implementación, anterior a la corrección P3 de pruebas

El gate satisfactorio final fue el de **457 pruebas**. Esta ejecución y sus
coberturas preceden la corrección P3 descrita debajo; no son resultados posteriores
a esa corrección de pruebas. No se ha modificado código de ejecución desde ese gate.

| Verificación posterior a ambas correcciones | Exit | Resultado |
| --- | --- | --- |
| Alembic upgrade head en PostgreSQL tmpfs nuevo | 0 | Revisiones 01–06 |
| Ruff format/check de los archivos pendientes | 0 | 3 formateados; lint PASS |
| Pruebas focalizadas, sin cobertura global parcial | 0 | 50 passed, 38,28 s |
| .\scripts\backend-quality.ps1 (build completo) | 0 | 457 passed, 99,58 s |
| Ruff format --check y ruff check dentro del gate | 0 cada uno | 87 archivos; PASS |
| Cobertura global de sentencias | 0 | 2.113/2.120 = 99,67% |
| Cobertura global de ramas | 0 | 397/404 = 98,27% |
| pip-audit 2.10.1, requirements.txt | 0 | No known vulnerabilities found |
| Secret scan del repositorio | 0 | Sin patrones de alta confianza |
| Storage sin red, módulos auditados de etapa 1 | 0 | 95 passed; 357/357 sentencias, 66/66 ramas: 100% ambas |
| Comparación OpenAPI explícita | 0 | OPENAPI MATCH operations=5 |
| pip check en imagen final, sin red | 0 | No broken requirements found |
| Exclusiones en imagen de calidad final | 0 | Sin .evidence, .kek ni locks de evidencia |
| test-package-file-policy.ps1 | 0 | 24 rutas rechazadas, 4 fuentes conservadas; sin ZIP |
| git check-ignore --no-index -v | 0 | 8 rutas representativas excluidas |
| git diff --check | 0 | Sin errores de whitespace |

Se mantuvieron los umbrales: cobertura global y cobertura de ramas >=90%; no se
relajó pyproject.toml ni el gate. La suite sin red mide explícitamente contracts,
crypto, filesystem, configuration y storage: los nuevos servicios que necesitan
PostgreSQL se incluyen en la cobertura global, no en ese subconjunto sin DB.

Las 50 focalizadas incluyen 47 de servicio y 3 de migración: fresh upgrade,
upgrade desde 05 con activo unverified existente, convergencia histórica completa
de schema/grants, downgrade vacío y rechazo de downgrade con operaciones. Las FK,
RLS y grants se ejercitan con runtime y un LOGIN operacional reales, no simulados.

El caso de SQL directo contiene evidencia confirmada real del segundo tenant;
al falsificar app.organization_id, mantenimiento no ve las tres tablas de datos y
no modifica filas. No puede añadir su propia asignación. Sobre su tenant permitido,
un intento directo de confirmar prepared falla con SQLSTATE 42501. Las pruebas de
fsync fallan antes y después de invocar el fsync real, repiten con el archivo ausente
y comprueban que objeto/bytes reservados solo se liberan tras un reintento exitoso.

Los warnings del gate son la deprecación existente de Starlette/httpx y los dos
RuntimeWarning de runpy en CLI de bootstrap/onboarding. No hubo pruebas fallidas.
Git avisa de conversión futura LF/CRLF; diff --check devuelve 0.

No se repitieron smokes HTTPS: get_actor, endpoints, configuración de autenticación,
Compose, Dockerfile y contrato servido no se modificaron. .dockerignore solo añade
la exclusión del lock nuevo. El gate mantiene las regresiones de autenticación,
CSRF, RBAC, RLS e incremento 1; no se reutiliza evidencia HTTPS como validación nueva.

### Comandos y registros reproducibles

Desde C:\SentinelAI\platform, la base focalizada fue el proyecto exacto
sentinelai-evidence-stage2-check de compose.quality.yaml, sin puertos host y con
PostgreSQL tmpfs. El gate crea su propio proyecto aleatorio y lo elimina en finally.

```powershell
docker compose -p sentinelai-evidence-stage2-check -f compose.quality.yaml up -d --wait postgres
docker compose -p sentinelai-evidence-stage2-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace -e DATABASE_URL=postgresql+psycopg://sentinelai_migrator:ephemeral-test-only@postgres:5432/closure_test quality alembic upgrade head
docker compose -p sentinelai-evidence-stage2-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace quality pytest -q --no-cov -p no:cacheprovider tests/test_evidence_service.py tests/test_evidence_migration.py tests/test_asset_migration.py tests/test_migration_convergence.py --tb=short
.\scripts\backend-quality.ps1
docker run --rm --name sentinelai-evidence-stage2-storage --network none --read-only --tmpfs /tmp:rw,nosuid,size=128m,uid=10001,gid=10001 -e COVERAGE_FILE=/tmp/.coverage -v "${PWD}/backend:/workspace:ro" -w /workspace sentinelai-closure-quality:local pytest storage_tests -q -p no:cacheprovider -o 'addopts=--strict-config --strict-markers --cov=app.evidence.contracts --cov=app.evidence.crypto --cov=app.evidence.filesystem --cov=app.evidence.configuration --cov=app.evidence.storage --cov-report=term-missing --cov-fail-under=90'
.\scripts\test-package-file-policy.ps1
git check-ignore --no-index -v -- x/opaque.evidence x/lab-1.kek x/stage-0123456789abcdef.tmp x/.evidence.lock x/.evidence.coordinator.lock evidence-data/opaque evidence-storage/opaque evidence-keys/opaque
git diff --check
docker compose -p sentinelai-evidence-stage2-check -f compose.quality.yaml down --remove-orphans
```

OpenAPI se comparó en sentinelai-evidence-stage2-contract, contenedor --rm,
--network none, --read-only, tmpfs /tmp, ENVIRONMENT=test, sobre la imagen final.
Se ejecutó pip check y después este Python vía stdin (sin escribir snapshot):

```python
import json
from pathlib import Path
from app.core.config import get_settings
from app.main import app
from tests.assets_contract import assets_contract
actual = assets_contract(app.openapi(), get_settings().csrf_header_name)
expected = json.loads(Path('tests/contracts/assets.openapi.json').read_text())
assert actual == expected
print('OPENAPI MATCH operations=' + str(sum(len(v) for v in actual['paths'].values())))
for path in Path('/workspace').rglob('*'):
    assert path.suffix not in {'.evidence', '.kek'} and path.name not in {
        '.evidence.lock', '.evidence.coordinator.lock'
    }
print('BUILD IMAGE EXCLUSIONS PASS')
```

Logs posteriores al corte, en audit-results/:

- sprint-2a-stage2-resume-focused.log
- sprint-2a-stage2-resume-quality.log
- sprint-2a-stage2-resume-storage.log
- sprint-2a-stage2-resume-contract.log
- sprint-2a-stage2-resume-cleanup.json

El log del gate final comienza 2026-10-06T15:28:12Z y termina tras pip-audit/secret
scan y cleanup. Las cifras previas 455/456 no se presentan como resultados finales.

## Corrección P3 de pruebas y validación posterior

La auditoría observó que los tres intentos de sustitución reutilizaban una operación
ya referenciada y aceptaban cualquier DBAPIError. Podían fallar por UNIQUE sin
demostrar la FK compuesta. Se retiró ese bucle de la prueba de RLS/grants y se añadió
test_reference_substitution_reaches_composite_fk, parametrizada por tenant, activo
y versión, con un fixture nuevo por caso.

El fixture reserva mediante el servicio real, prepara y escribe un envelope cifrado
válido fuera de la transacción DB y construye el estado committed sin referencia con
la credencial administrativa efímera. Conserva triggers, constraints y contadores
de cuota. Es un fixture de integridad referencial; no modifica ni reproduce la
confirmación de producción como sustituto de sus pruebas de atomicidad existentes.
Cada operación no ha sido referenciada previamente. Las sustituciones de tenant y
activo apuntan a activos existentes de la organización correspondiente; la versión
alternativa es positiva y no está ocupada.

Cada caso inserta primero la referencia correcta como control positivo y revierte
esa transacción. Comprueba después ausencia de referencias y existencia del activo
destino antes del intento inválido en una conexión/transacción nueva. Exige
SQLSTATE **23503** y el nombre exacto
**evidence_versions_organization_id_operation_id_asset_id_ve_fkey**, corroborado
en pg_constraint de PostgreSQL 17.6 tras upgrade 01–06. Así ni UNIQUE(operation_id),
ni la PK de referencias, ni la FK de activos pueden enmascarar el rechazo esperado.
Los tres casos pasan y cada fixture se aísla mediante la limpieza de pytest.

Solo se editaron backend/tests/test_evidence_service.py y este handoff. Los hashes
de migración 06, coordinación, mantenimiento, servicio, pruebas de migración y
documentación del protocolo se mantuvieron iguales al checkpoint de la auditoría.
El diff de los seis archivos ya modificados se conserva. Sin cambios de ejecución,
FK, migraciones, autenticación, despliegue o contrato servido.

Resultados obtenidos **después de la última edición de la prueba**:

| Comando/verificación | Exit | Resultado |
| --- | --- | --- |
| compose up -d --wait postgres; alembic upgrade head | 0 cada uno | PostgreSQL 17.6 efímero tmpfs, revisiones 01–06 |
| ruff format tests/test_evidence_service.py | 0 | 1 archivo formateado antes de validar |
| ruff format --check tests/test_evidence_service.py | 0 | Formato correcto |
| ruff check tests/test_evidence_service.py | 0 | All checks passed |
| pytest con -k reference_substitution_reaches_composite_fk | 0 | 3 passed, 47 deselected, 1,60 s |
| pytest tests/test_evidence_service.py completo | 0 | 50 passed, 14,18 s |
| Consultas de cleanup en PostgreSQL | 0 | test_roles=0, temporary_databases=0, operational_assignments=0 |
| compose down --remove-orphans del proyecto P3 | 0 | Contenedor PostgreSQL y red retirados |
| Consultas Docker por etiqueta exacta | 0 | 0 contenedores, 0 redes, 0 volúmenes |
| git diff --check | 0 | Sin errores de whitespace |
| git diff --no-index --check -- NUL, para cada fuente editada aún no rastreada | 1 cada uno | Diferencia contra archivo vacío; sin diagnósticos de whitespace |

Las 50 pruebas actuales son de servicio (47 existentes y 3 casos nuevos); no son
las 50 focalizadas históricas que incluían 3 pruebas de migración. El único warning
es la deprecación existente de Starlette/httpx. No se repitió el gate completo ni
se recalculó cobertura global: solo cambiaron pruebas/documentación y las
focalizadas pasaron, conforme al alcance solicitado. Los umbrales permanecen
intactos. Las cifras 99,67% de sentencias y 98,27% de ramas pertenecen al gate
histórico satisfactorio de 457. No se repitieron smokes HTTPS, storage sin red,
pip-audit ni secret scan; sus resultados anteriores conservan esa procedencia.

Comandos desde C:\SentinelAI\platform; proyecto exclusivo
sentinelai-evidence-p3-check, sin puertos host ni volúmenes persistentes:

```powershell
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml up -d --wait postgres
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace -e DATABASE_URL=postgresql+psycopg://sentinelai_migrator:ephemeral-test-only@postgres:5432/closure_test quality alembic upgrade head
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace" -w /workspace quality ruff format tests/test_evidence_service.py
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace quality sh -c "ruff format --check tests/test_evidence_service.py && ruff check tests/test_evidence_service.py"
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace quality pytest -q --no-cov -p no:cacheprovider tests/test_evidence_service.py -k reference_substitution_reaches_composite_fk --tb=short
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml run --rm -T --no-deps -v "${PWD}/backend:/workspace:ro" -w /workspace quality pytest -q --no-cov -p no:cacheprovider tests/test_evidence_service.py --tb=short
docker compose -p sentinelai-evidence-p3-check -f compose.quality.yaml down --remove-orphans
git diff --check
git diff --no-index --check -- NUL backend/tests/test_evidence_service.py
git diff --no-index --check -- NUL docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_2_HANDOFF.md
```

Logs locales ignorados: audit-results/sprint-2a-stage2-p3-format.log,
sprint-2a-stage2-p3-ruff.log, sprint-2a-stage2-p3-fk.log,
sprint-2a-stage2-p3-service.log, sprint-2a-stage2-p3-cleanup.log y
sprint-2a-stage2-p3-down.log. Los fixtures retiraron directorios y llaves y
comprobaron su ausencia; los runners --rm están ausentes. Solo se retiró el
proyecto exacto P3, sin operar almacenamiento real ni recursos de otros proyectos.

Git antes/después de P3: feature/sprint-2a-assets-scope, HEAD
894268192b1b5de96b010eed6216b82483d7b6fa, **6 modificados + 8 nuevos**, índice
vacío. Ambas fuentes editadas ya figuraban como nuevas. Sin stage, commit, push,
merge, paquete ni avance de etapa. La corrección queda para revisión focalizada.

## Cleanup y estado final

El proyecto heredado sentinelai-evidence-stage2-check fue identificado y retirado
con compose down --remove-orphans antes de recrear su PostgreSQL tmpfs para probar
la revisión 06 final. No se han operado otros proyectos ni volúmenes persistentes.
Antes de retirarlo se consultó pg_roles, pg_database y evidence_maintenance_tenants:
test_roles=0, temporary_databases=0 y operational_assignments=0. Los fixtures borran
sus directorios/llaves y comprueban ausencia; los contenedores runners son --rm.

Consulta Docker por etiqueta exacta posterior al gate: **0 contenedores, 0 redes y
0 volúmenes** para cada proyecto de esta tarea:

- sentinelai-evidence-stage2-check
- sentinelai-closure-quality-1e7fdaf0e4 (gate previo de 455)
- sentinelai-closure-quality-f67a7f4669 (gate de 456 completado durante el corte)
- sentinelai-closure-quality-c287c91fd2 (gate final de 457)

Los runners nominados sentinelai-evidence-stage2-storage y
sentinelai-evidence-stage2-contract también están ausentes. Se conserva la imagen
local normal de calidad y su caché de build; no son almacenamiento operativo.

Git final: misma rama, HEAD y referencia local origin/feature/sprint-2a-assets-scope
en 894268192b1b5de96b010eed6216b82483d7b6fa; no se hizo fetch ni se afirma estado
remoto vivo. Índice vacío; **6 modificados y 8 nuevos**. No se hizo stage, commit,
push, merge, paquete ni cambio de rama. Solo el handoff se añadió al inventario de
la reanudación. Logs ignorados no cuentan como fuentes nuevas del diff.

Archivos modificados:

- .gitignore
- backend/.dockerignore
- backend/quality_gate.py
- backend/tests/test_asset_migration.py (aserción de rollback a revisión inicial)
- scripts/package-file-policy.ps1
- scripts/test-package-file-policy.ps1

Archivos nuevos:

- backend/alembic/versions/20261005_06_evidence_operations.py
- backend/app/evidence/coordination.py
- backend/app/evidence/maintenance.py
- backend/app/evidence/service.py
- backend/tests/test_evidence_migration.py
- backend/tests/test_evidence_service.py
- docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_2.md
- docs/engineering/SPRINT_2A_INCREMENT_2_STAGE_2_HANDOFF.md

Se comprobó con git diff --exit-code HEAD que migraciones 01–05, snapshots
históricos/OpenAPI, módulos auditados de etapa 1 y app/api no tienen cambios.

## Límites y reanudación

Linux local y filesystem compatible con descriptores, nofollow, flock, hard links y
fsync. Windows nativo sigue fallando cerrado; Windows bind mounts no están soportados.
Las pruebas usan Docker Desktop Linux y tmpfs. Demuestran recuperación ante fallos
inyectados de proceso/I/O/commit, **no durabilidad ante pérdida de energía** en un
volumen persistente. Tampoco hay transacción atómica PostgreSQL/filesystem ni
coordinación distribuida entre hosts o raíces diferentes. Procesos del mismo UID,
root, administrador DB y host siguen dentro de la frontera de confianza.

La provisión operacional real de LOGIN/asignaciones/roots/KEK queda fuera de estas
pruebas; solo se crean credenciales y llaves efímeras. No hay retención automática,
rotación, backup/restore, workflow humano ni autorización para escanear. El único
paso posterior previsto es auditoría independiente del diff, sin commit automático.
