# Sprint 2A / incremento 2 / etapa 4B — handoff

Implementación/validaciones iniciales: 2026-10-09; recuperación y entrega:
2026-10-10 (America/Lima). Base: `e45bd7dfe2d057d819a210de860712d291043727`,
`feature/sprint-2a-assets-scope`. Se verificaron árbol e índice limpios y referencia
origin coincidente antes de implementar; al reanudar se conservó el diff esperado.
Sin nuevas ramas, stage, commit, push, merge ni paquete. No se inicia otra etapa.

**4B implementada para auditoría independiente.** Validación por recuperación de
etapas: el wrapper del gate v2 no tiene exit final recuperable; no se le atribuye
exit 0. Sus controles concluidos se reutilizan por coincidencia de hashes y los
controles pendientes se completan por separado. No es aprobación para producción.

## Implementado y fronteras

- Contrato previo: [SPRINT_2A_INCREMENT_2_STAGE_4B.md](SPRINT_2A_INCREMENT_2_STAGE_4B.md).
  Guía para revisión real: [STAGE_4B_HUMAN_API_RUNBOOK.md](STAGE_4B_HUMAN_API_RUNBOOK.md).
- Solicitudes inmutables sobre evidencia confirmada propia, versión exacta,
  snapshot/versiones del activo, generación persistente y retención original.
- Revisión owner/manager distinta de presentador/autor por user_id, lectura auditada
  posterior y checklist cerrado. Se conserva permiso `evidence:read` al decidir.
  Literal numérico de versión rechaza booleanos, floats y strings explícitamente.
- Una pendiente por activo; decisión terminal única; retiro propio, renovación
  enlazada, sustitución permanente, revocación y expiración sin reescribir decisiones.
- La consulta de vigencia lee/descifra ahora fuera de locks y revalida después;
  devuelve checked_at y razones. Un estado histórico/replay no prueba vigencia.
- Archivo/cambio de admisión invalidan dentro de la transacción original mediante
  triggers de 09. Nombre/criticidad compatibles no invalidan. Retirar/readmitir con
  el mismo hash no resucita aprobación. Mantiene `ownership_status=unverified`.
- JSON streaming <=16 KiB, CSRF, Origin opcional validado, If-Match e idempotencia
  de 24 h por actor/tenant/método/ruta/payload/versión. Replay contiene solo IDs.
  Commit incierto devuelve 503 sanitizado; reintento de la misma intención recupera
  historia sin duplicados. No filesystem/descifrado bajo transacciones de revisión.

No scanner, conexiones a objetivos/SENATI, evaluate, permisos de escaneo, workers,
outbox publisher ni UI completa. No se certifica propiedad, verdad del testimonio,
dos personas distintas, aprobación institucional o disponibilidad para producción.

## PostgreSQL y privilegios

09 depende de 08; las ocho migraciones entregadas permanecen intactas. Añade
`control_review_requests`, `control_review_projections`, `control_review_events`,
FK compuestas a activo/evidencia/generación/revisión/lectura auditada, RLS ENABLE y
FORCE, índices únicos de pendiente/tipo/terminal, y grants de INSERT por columnas.
Runtime no tiene UPDATE/DELETE sobre historia o proyección. Identidad, timestamps,
snapshot y referencias a lectura real se derivan en SQL, no del body.

Funciones SECURITY DEFINER con search_path fijo y ejecución pública revocada
verifican sesión, tenant, roles, permisos, separación, versión, lectura, catálogo
y vigencia. Los triggers generan proyección/auditoría v3/outbox en la transacción de
la mutación. `verify_chain` se prueba con eventos mixtos Python/SQL. El publicador
4A no recibe privilegios sobre revisiones; sus constraints auditados siguen activos.
El downgrade rechaza cualquier historia de revisión; solo baja 09 si está vacía.

Frontera explícita: runtime y DBA son componentes confiables, como en RLS existente.
Sus GUC no son una credencial humana autónoma; SQL no prueba lectura física ni
veracidad humana. La aplicación comprueba almacenamiento real. No se afirma defensa
contra un DBA o contra ejecución arbitraria con todas las credenciales de runtime.

## Inventario del diff

Nuevos:

- `backend/alembic/versions/20261009_09_control_reviews.py`.
- `backend/app/control_reviews/{__init__,schemas,service}.py`.
- `backend/app/api/v1/control_reviews.py`.
- `backend/tests/test_control_{contract,migration,reviews,reviews_sql}.py`.
- `backend/tests/contracts/control.openapi.json` (ocho operaciones congeladas).
- `scripts/control-review-smoke.ps1`.
- Los tres documentos 4B de este directorio, incluido este handoff.

Modificados:

- `backend/app/api/v1/router.py`, `backend/app/main.py`, `backend/quality_gate.py`.
- `backend/tests/assets_contract.py`: selecciona las cinco operaciones originales;
  snapshots de activos/evidencia NO se reescriben. 4B tiene su congelación separada.
- `backend/tests/test_policy_migration.py`: baja primero la 09 vacía para probar
  explícitamente la negativa histórica de downgrade de 08, sin quitar su aserción.
- `scripts/authenticated-compose-smoke.ps1` y su helper Python: opción ControlReviews,
  cuentas técnicas A/B/C, dos tenants, cleanup y marcadores completos.

Git es el inventario autoritativo; `git diff --stat` no incluye archivos nuevos.
`audit-results` contiene evidencia local ignorada, nunca entregable del paquete.

## Evidencia y procedencia

Las 607 pruebas de 4A son históricas y no validan este código. Los primeros intentos
4B también se conservan separados, sin convertirlos en resultados definitivos:

| Ejecución | Exit | Resultado comprobado |
| --- | --- | --- |
| Focalizada inicial | 1 | 36 pass / 4 fallos de fixtures/aserciones nuevas |
| Focalizada v2 | 1 | 69 pass / 2 expectativas de archivo (204, no 200) |
| Focalizada v3 | 1 | 77 pass / 1 fixture 08 sin publicación explícita |
| Gate intento 1 | 1 | 690 pass; sentencias 99,26%, ramas 96,24%; Ruff format falló |
| HTTPS control intento 1 | 0 | PASS técnico, anterior a cambios finales |
| HTTPS base intento 1 | 1 | Seed recibió null en lista de revisores; cleanup aprobado |

Se corrigieron esos defectos sin relajar reglas. El fixture de 08 ahora publica
política sintética con LOGIN restringido; no introduce fallback ni desactiva guards.
El helper tolera ausencia de revisores en el smoke base. La integración inicial
corrigió la separación entre el contexto de identidad temporal y el contexto SQL
de revisión; no amplió UPDATE para bloquear la proyección.

Logs completos existentes bajo `audit-results/`:

- `stage4b-focused-initial.log`, `stage4b-focused-v2.log`, `stage4b-focused-v3.log`.
- `stage4b-final-gate.log` (fallido), `stage4b-final-gate-v2.log` (interrumpido tras
  pytest/pip check, al iniciar pip-audit; sin marcador final del wrapper).
- `stage4b-final-https-default.log` (fallido), `stage4b-final-https-control.log`
  (intermedio), y ambos `stage4b-final-https-*-v2.log` (finales).
- `stage4b-final-storage.log`, `stage4b-final-exclusions.log`,
  `stage4b-final-lint-helper.log`.

Los smokes v2 han terminado exit 0, con `AUTHENTICATED COMPOSE SMOKE: PASS`;
el de control también contiene `CONTROL REVIEW HTTPS SMOKE: PASS`. Verifican
HTTPS, cuentas técnicas A/B/C, otra organización, separación, lectura, replay,
revocación, retiro y aislamiento. No constituyen una aceptación por dos personas.
El certificado es interno efímero y el harness omite validación de su CA **solo en
el cliente smoke**; no acredita una cadena pública de certificados de producción.

Storage sin red: 95 pass, exit 0. Exclusiones: 27 denegadas / 4 fuentes retenidas,
exit 0, sin ZIP. Ruff del helper: exit 0. En todos los casos el wrapper ignorado
`stage4b-validate.ps1` captura todos los streams y escribe el exit real del hijo.

### Resultado definitivo y comandos

El 10 de octubre se recuperaron 700 pruebas terminadas con exit 0 en v2: **700
aprobadas, 0 fallidas, 0 omitidas; sentencias 99,26%, ramas 96,26%** (umbral >=90%
intacto). Los tres warnings son la deprecación de TestClient/httpx y dos avisos
runpy de módulos ya importados; no se silenciaron. No se repitió pytest.

| Comando/control | Exit | Evidencia y resultado |
| --- | --- | --- |
| `alembic upgrade head` | 0 | v2: instalación limpia hasta 09 |
| `ruff format --check <lint_paths de quality_gate.py>` | 0 | v2: 111 archivos conformes |
| `ruff check <lint_paths de quality_gate.py>` | 0 | v2: sin findings |
| `pytest -q -p no:cacheprovider --cov-report=json:/tmp/coverage.json` | 0 | v2: 700 pass; incluye contratos, SQL, fresh/08 poblada/convergencia y downgrade |
| `pip-audit --version` / `pip check` | 0 / 0 | v2: 2.10.1; sin dependencias rotas |
| `pip-audit --progress-spinner=off -r requirements.txt` | 0 | Reanudado 10/10 en la misma imagen exacta: sin vulnerabilidades conocidas |
| `authenticated-compose-smoke.ps1` | 0 | HTTPS base v2, PASS y cleanup |
| `authenticated-compose-smoke.ps1 -ControlReviews` | 0 | HTTPS control v2, ambos marcadores PASS y cleanup |
| `pytest storage_tests --no-cov -q -p no:cacheprovider` con `--network none` | 0 | 95 pass |
| `test-package-file-policy.ps1` | 0 | 27 exclusiones / 4 fuentes; no crea ZIP |
| `ruff check /scripts/authenticated-compose-smoke-helper.py` | 0 | Helper conforme |
| `python /repository/scripts/secret_scan.py /repository` en la imagen exacta, sin red | 0 | Reanudado 10/10: sin patrones de alta confianza; sin relajar exclusiones |
| Cleanup de proyectos propios y consulta de procesos | 0 | Siete proyectos sin contenedores/redes/volúmenes; cero procesos de validación activos |

`backend-quality.ps1` se lanzó mediante `pwsh -NoProfile -File
audit-results/stage4b-validate.ps1 -Mode gate -RunId v2`. El log termina durante
pip-audit; no hay proceso ni runner original activo. Se preserva el log sin añadirle
un final artificial. La reanudación está en `stage4b-resumed-pip-audit.log`, con
comando completo y `STAGE4B_RESUMED_PIP_AUDIT_EXIT=0`. La cobertura y pruebas del
intento 1 no sustituyen los resultados v2.

Casos 4B comprobados: evidencia propia/ajena, autoaprobación entre sesiones y roles,
lectura posterior, checklist/tipos estrictos, permisos y RLS con LOGIN runtime,
restricciones SQLSTATE/constraint específicas, retención y límite de 30 días,
metadatos compatibles, archivo/generación/readmisión, renovación/rechazo/retiro,
sustitución/revocación, dos revisores concurrentes, replay simultáneo, pérdida de
sesión/rol/permisos/política durante I/O, corrupción/ausencia de storage/llave,
rollback y commit incierto tanto de solicitud como de decisión. Las pruebas de 4A
se ejecutaron también como regresiones dentro de las 700, no como evidencia heredada.

## Vinculación del estado validado

`audit-results/stage4b-source-manifest-v2.json`: 151 fuentes/configuraciones,
SHA-256 `98A8CF5265A1040701845BE456363AED1AF85904514A1CC8E92D120EDA2D7FEC`.
Se contrastó el manifiesto con el árbol actual. Cambios documentales posteriores se
separan: no forman parte de las imágenes ni alteran el código probado.

`stage4b-image-source-verification.json` compara bytes de imagen con workspace:
125 archivos quality, 81 backend. Imágenes comprobadas:

- quality: `sha256:7f18d7633140ff090b7ed449609b67799d2b6a5371720951a0fe837aafa81061`.
- backend: `sha256:fc860f493a9eda4110d6956e704b0ef60725eb84a4088a48e9bffc7f193de335`.

SHA-256 de migración 09:
`CAA9ADC3D5B2D76CDC0B56D16D5866F56741B28A8BCC4AD85D91AE09AC752989`.
Contrato OpenAPI:
`E626026DF8D4D1CF7F0577B222D325B652C247B60FC1F36DA29104E9557B81FE`.
Hashes de 01–08 en `stage4b-original-migrations.json`, sin diff contra HEAD.

## Cierre operativo y límites

El detalle final de escaneo del repositorio, integridad de manifiestos/Git y recursos
se registra en `stage4b-resumed-repository-scan.log`, `stage4b-final-integrity.log` y
`stage4b-resumed-cleanup-v2.log`. No se genera un paquete. `audit-results` permanece
ignorado y fuera de los contextos/copias de imagen y de la política de paquete.

El primer intento de cleanup del 10/10 retiró únicamente `sentinelai-stage4b-dev` y
se detuvo correctamente antes de tocar `sentinelai-closure-quality-91445536e9`:
el nuevo runner pip-audit heredaba esa etiqueta de la imagen y estaba activo. Se
esperó su finalización exit 0 antes de continuar. No se interrumpió ese proceso.
Se conservó `stage4b-resumed-cleanup.log` como registro parcial, no como cleanup final.

Se conservan imágenes cacheadas para reproducción; no son runners activos ni se
realizó prune global. Los proyectos operativos preexistentes no se gestionaron.
El escaneo de secretos es el detector limitado de alta confianza existente, no
una certificación exhaustiva de ausencia de secretos. No hubo una reejecución del
wrapper completo: se completó su matriz de controles con procedencia explícita.
Git al entregar: misma rama/HEAD, índice vacío, **7 archivos modificados y 14 nuevos**.
Las 01–08 no tienen diff. Los únicos cambios de esta reanudación son documentación
y evidencia local ignorada; schemas/router/migración/helpers coinciden con v2.
`stage4b-delivery-files.json` registra hashes de las 21 rutas del diff, incluidos
los documentos finales; `stage4b-evidence-files.json` registra los logs y manifiestos
locales existentes sin inventar salidas que no se capturaron.

Pendientes fuera de 4B automatizada: auditoría independiente y aceptación observada
por dos personas reales. No se acredita esa aceptación con fixtures ni se inicia
otra etapa. No se afirma durabilidad de backups, operación de rotación de llaves,
autorización de escaneo o readiness de producción.
