# Sprint 2A — incremento 1: resultados verificables

Estado: INCREMENTO 1 COMPLETO — LISTO PARA REVISIÓN HUMANA.
No es cierre de todo Sprint 2A ni autorización de escaneo/producción.

## Estado Git y recuperación

- Repositorio: `C:\SentinelAI\platform`.
- Checkpoint: `55022c4eff2bc527c834edd0c43b130ecd60ddd0`.
- Antes de implementar se comprobaron rama de cierre, HEAD, árbol limpio y
  `git ls-remote origin refs/heads/remediation/sprint-1b1-closure-gates`: coincidían.
- Se creó únicamente la rama local `feature/sprint-2a-assets-scope`.
- Al retomar: esa misma rama/HEAD, seis archivos tracked modificados y nuevas
  fuentes/pruebas sin seguimiento; se conservaron sin restaurar ni recrear rama.
- Se recuperó evidencia previa de 65 pruebas dirigidas aprobadas; el proceso del
  gate interrumpido ya no existía. No se adjudica un resultado final a ese proceso.
- Docker conservaba PostgreSQL detenido (exit 255) y una red del proyecto
  `sentinelai-assets-devtest`, sin volúmenes. Se retiraron exclusivamente esos
  recursos y se ejecutaron gates nuevos en proyectos aleatorios.
- Sin commit, staging, push, merge, rebase, ZIP ni cambios en la rama de cierre.
  HEAD sigue en el checkpoint; modificaciones y archivos nuevos quedan en worktree.

## Entrega funcional

1. Política local explícita y estricta, vacía por defecto; exact IPv4 allowlist,
   exclusiones, bloqueo de rangos especiales y máximo de activos activos/tenant.
2. Cinco operaciones HTTP de inventario: crear, listar, consultar, editar
   metadatos con If-Match y archivar con motivo/versión. IP inmutable.
3. Todos los activos `unverified`; ninguna afirmación de propiedad o autorización.
4. RLS forzado, scoping de repositorios, permisos por acción, cuotas serializadas,
   grants mínimos, FK tenant compuesta y trigger de inmutabilidad/versiones.
5. POST/DELETE con replay HTTP 24h, validación previa de actor/CSRF/permisos,
   fingerprint, conflicto y caducidad. Separado de las claves internas del outbox.
6. Mutación/auditoría/outbox/replay atómicos. No publisher ni workers.
7. OpenAPI real de activos exportado, versionado y comprobado contra código.

Contrato y decisiones: [SPRINT_2A_INCREMENT_1.md](SPRINT_2A_INCREMENT_1.md).

## Correcciones comprobadas durante el trabajo

- Ruff inicialmente encontró formato/longitud/imports en archivos nuevos: se
  corrigieron sin bajar reglas ni modificar revisiones entregadas.
- Tras retomar, primer gate: 307 passed, sentencias 99.59%, ramas 97.78%, exit 0.
- Un ensayo adicional sin red reprodujo `AttributeError` con un cursor cuyo
  identificador JSON era entero. Se corrigió validando estructura/lista/tipos antes
  de UUID. Cuatro pruebas ahora exigen 422 INVALID_CURSOR, nunca error interno.
- Se completaron errores/descripciones OpenAPI y prueba del contrato exportado.
- El smoke filtraba localmente por resource_id pero enviaba un query parameter que
  la API de auditoría no soporta. Se retiró ese parámetro, conservando la selección
  local y las aserciones de exactamente un evento de cada mutación.

## Comandos y resultados nuevos

Ejecución final del gate: 2026-09-25 03:37–03:39 UTC (24 de septiembre en Lima).
Los comandos siguientes se ejecutaron; los subcomandos se muestran como los
invocó el gate dentro de Docker, no como ejecuciones locales de Python.

| Comando | Exit status | Resultado |
| --- | --- | --- |
| `git branch --show-current` / `git rev-parse HEAD` / `git status --short` | 0 cada uno | Rama/HEAD correctos; cambios existentes preservados |
| `docker compose -p sentinelai-assets-devtest -f compose.quality.yaml down --remove-orphans` | 0 | Restos del entorno interrumpido eliminados; sin volúmenes |
| `.\scripts\backend-quality.ps1` (primer gate nuevo) | 0 | 307 passed; Ruff, audit de dependencias, secretos y cleanup PASS |
| `.\scripts\backend-quality.ps1` (gate final, tras cursor/OpenAPI) | 0 | 312 passed; resultados definitivos abajo |
| `alembic upgrade head` dentro del gate final | 0 | Base limpia 01→02→03→04→05 |
| `ruff format --check app tests alembic/env.py alembic/versions/20260920_04_closure_security_convergence.py alembic/versions/20260924_05_lab_assets.py quality_gate.py` | 0 | 71 archivos con formato correcto |
| `ruff check app tests alembic/env.py alembic/versions/20260920_04_closure_security_convergence.py alembic/versions/20260924_05_lab_assets.py quality_gate.py` | 0 | All checks passed |
| `pytest -q -p no:cacheprovider --cov-report=json:/tmp/coverage.json` | 0 | 312 passed, 4 warnings, 71.72 s |
| `pip-audit --version` | 0 | 2.10.1 |
| `pip-audit --progress-spinner=off -r requirements.txt` | 0 | No known vulnerabilities found |
| `python /repository/scripts/secret_scan.py /repository` dentro del gate | 0 | No high-confidence patterns detected |
| `.\scripts\authenticated-compose-smoke.ps1 -Assets` (versión final) | 0 | HTTPS assets y controles de foundation PASS; cleanup PASS |
| `.\scripts\authenticated-compose-smoke.ps1` (versión final) | 0 | Política vacía deniega incluso a platform_admin; foundation/cleanup PASS |
| `docker compose --env-file .env.example config --quiet` | 0 | Configuración válida, sin objetivos por defecto |
| `git diff --check` | 0 | Sin errores de whitespace en diff tracked |
| `git diff --name-only <checkpoint> -- <revisiones 01–04 y tres snapshots históricos>` | 0, salida vacía | Artefactos históricos sin cambios |

Los fallos iniciales de acceso a Docker/Git en sandbox se resolvieron mediante
permisos explícitos. No se interpretaron como resultados de prueba.

## Pruebas y cobertura finales

| Métrica | Resultado |
| --- | --- |
| Pruebas ejecutadas | 312 |
| Passed | 312 |
| Failed | 0 |
| Skipped | 0 |
| Sentencias | 1478/1484 = 99.60% |
| Ramas | 266/272 = 97.79% |
| Umbral obligatorio de ramas | >=90%, conservado |
| Cobertura combinada mostrada por pytest-cov | 99.32%; no confundir con sentencias |

Se conservaron los 229 casos foundation y se añadieron 83 casos en este incremento.
Las fuentes nuevas de activos/idempotencia aparecen con cobertura completa en el
reporte; la cobertura por sí sola no demuestra ausencia de defectos.

Cuatro warnings no silenciados: dos de deprecación Starlette/httpx/AnyIO y dos de
runpy en las pruebas CLI existentes de bootstrap/onboarding. No se cambió ninguna
dependencia ni se suprimieron warnings para obtener PASS.

Casos cubiertos:

- Política ausente, vacía, inválida, duplicados JSON, booleanos/decimales indebidos,
  allowlist/exclusiones, IPv4 ambigua, dominio/URL/IPv6/rango y authority injection.
- Matriz de roles, CSRF, Origin, sesión revocada y downgrade de permiso antes de replay.
- Dos tenants, 404 cross-tenant, RLS runtime sin contexto/contexto ajeno, FK tenant,
  grants UPDATE exactos, imposibilidad de DELETE y de modificar identidad/IP.
- Duplicados, cuota, paginación/filtros, cursor inválido, versión obsoleta,
  metadata patch, archivo, recreación con otro ID y prohibición de restauración.
- Replay POST/DELETE, conflicto de payload, aislamiento de clave por actor/tenant,
  expiración y reciclaje; fallo no consume clave ni genera cambios parciales.
- Creaciones concurrentes con misma clave: una mutación y un replay. Updates con
  misma versión: un éxito y un 409. Cuota concurrente: un éxito y un rechazo.
  Archivo concurrente: un éxito y un conflicto.
- Rollback de create/update/archive ante fallo de outbox, sin audit ni replay parcial.
- Integridad de cadena audit, eventos únicos y contrato OpenAPI contra archivo.
- Guardas de socket/resolución Python durante create/read/list/patch/archive:
  cero intentos hacia objetivos en ese camino probado. No se hizo captura de paquetes.

## Migración y grants

Revisión única nueva: `20260924_05_lab_assets.py`, down_revision `20260920_04`.
Tablas assets y http_idempotency_records, ambas FORCE RLS. Runtime SELECT/INSERT;
UPDATE solo columnas enumeradas; sin DELETE/DDL/BYPASSRLS/ownership nuevos.
Ningún permiso existente se relaja. Actor global se valida por sesión/membresía;
FK (organization_id,asset_id) impide referencias de replay entre tenants.

La suite ejecutó PostgreSQL real para:

- fresh 01→head;
- 04 con organización persistida→05, comprobando conservación del dato y permisos;
- convergencia esquema/grants entre fresh y upgrade;
- downgrade 05→04 con inventario vacío y re-upgrade;
- rechazo de downgrade con inventario, conservando fila y revisión 05;
- snapshots históricos 01/02→revisiones actuales→head comparados con fresh.

No se ensayó rollback destructivo sobre datos operativos. No existe una promesa
de downgrade sin pérdida de inventario; la migración lo rehúsa expresamente.

## Recursos efímeros

Consulta final mediante `docker ps -a -q`, `docker network ls -q` y
`docker volume ls -q`, filtradas por `label=com.docker.compose.project=<nombre>`:

| Proyecto | Contenedores | Redes | Volúmenes |
| --- | --- | --- | --- |
| sentinelai-assets-devtest | 0 | 0 | 0 |
| sentinelai-closure-quality-0bee30fa91 | 0 | 0 | 0 |
| sentinelai-closure-quality-fc1df6a19b | 0 | 0 | 0 |
| sentinelai-smoke-3aa4037ff9d1c221 | 0 | 0 | 0 |
| sentinelai-smoke-bcd2fbecbace2567 | 0 | 0 | 0 |
| sentinelai-smoke-fa377f9b9ccb7651 | 0 | 0 | 0 |

Todos los comandos de consulta terminaron en 0. Solo se destruyeron fixtures y
recursos desechables de estos proyectos. No se ejecutó down/prune sobre proyectos
operativos ni se borraron sus volúmenes. Se conservó la caché de imágenes de build.

## Inventario de archivos / diff

Tracked modificados (9):

- `.env.example`: política explícitamente vacía.
- `ROADMAP.md`: estado acotado de incremento 1.
- `backend/app/api/v1/router.py`: registra router de activos.
- `backend/app/main.py`: headers de idempotencia explícitos en CORS.
- `backend/app/platform/database/models.py`: Asset y HttpIdempotencyRecord.
- `backend/quality_gate.py`: incluye revisión 05 en Ruff.
- `compose.yaml`: transmite configuración de política, vacía por defecto.
- `docs/engineering/SPRINT_1B_OPERATIONS.md`: distingue replay nuevo de foundation.
- `scripts/authenticated-compose-smoke.ps1`: modo -Assets y prueba default-deny.

Nuevos (16), aún sin seguimiento:

- `backend/alembic/versions/20260924_05_lab_assets.py`
- `backend/app/api/v1/assets.py`
- `backend/app/assets/__init__.py`
- `backend/app/assets/configuration.py`
- `backend/app/assets/policy.py`
- `backend/app/assets/repository.py`
- `backend/app/assets/schemas.py`
- `backend/app/assets/service.py`
- `backend/app/platform/http_idempotency.py`
- `backend/tests/assets_contract.py`
- `backend/tests/contracts/assets.openapi.json`
- `backend/tests/test_asset_migration.py`
- `backend/tests/test_assets.py`
- `backend/tests/test_assets_contract.py`
- `docs/engineering/SPRINT_2A_INCREMENT_1.md`
- `docs/engineering/SPRINT_2A_INCREMENT_1_RESULTS.md`

`git diff --stat` muestra solo los 9 tracked: 180 inserciones y 3 eliminaciones.
Los 16 archivos nuevos no se incluyen en esa estadística porque no se hizo staging.
Para revisar la entrega hay que incluir también los untracked; no son archivos temporales.

## Límites y siguiente incremento

Política de registro no equivale a permiso de escaneo. Todos los activos siguen
unverified. Pendientes: evidencia cifrada, verificación humana, workflow de alcance
y elegibilidad. Frontend sigue con mocks; no se vuelve a certificar su lint/build
independientemente (el smoke usó la construcción Docker, con capas cacheadas).

Rate limiting es proceso-local; no hay purga automática de replay ni gestión
distribuida de política. Los nuevos errores no incorporan audit durable de denegación.
El esquema OpenAPI tipado se complementa con validadores semánticos (por ejemplo,
PATCH requiere al menos un campo no nulo), descritos en la guía de contrato.

No hay scanner, scanner simulado, destinos SENATI, jobs, IA, findings o publicación.
No se declara preparación para producción ni porcentaje de falsos positivos.
La siguiente acción es revisión humana de este diff, no ejecutar el incremento 2.
