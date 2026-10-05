# Sprint 2A incremento 2 — etapa 1: contrato y operación del storage

Biblioteca interna, no endpoint desplegado. No modifica el inventario, las
migraciones, CSRF/RBAC/RLS ni el esquema de auditoría/outbox existente.
Decisión: [ADR-009 laboratorio](ADR_009_LAB_STORAGE.md).

## Documento estricto

Solo JSON UTF-8, máximo 16 KiB antes de parsear. Claves duplicadas/extra, tipos
ambiguos, fechas sin zona, versiones distintas de 1 y JSON inválido se rechazan.
No se admiten archivos, imágenes, URLs, rutas, comandos, base64 o texto libre.

Campos exactos:

| Campo | Valor/validación |
| --- | --- |
| schema_version | entero exacto 1, no true ni 1.0 |
| method | supervised_local_console |
| observed_at | fecha consciente de zona, normalizada a UTC |
| lab_asset_reference | LAB- seguido de 1–6 dígitos; etiqueta del laboratorio |
| observations.console_identified | observed |
| observations.inventory_ipv4_matches | observed |
| observations.administrative_control | observed |
| declaration | technical_control_only_not_ownership_or_scan_permission |

parse_submission exige observación entre now-24h y now, inclusivos. La lectura
histórica valida el esquema, no aplica esa ventana de ingreso a un documento
antiguo. Ningún checkbox acredita por sí mismo que ocurrió una observación.

JSON canónico de aplicación: claves ordenadas recursivamente, separadores sin
espacios, ensure_ascii=true, sin NaN/infinito, datetime UTC serializado por el
modelo. No se declara RFC 8785. document_bytes vuelve a validar aun si se construyó
un modelo saltándose Pydantic. El hash del recibo cubre el envelope cifrado exacto.
La autenticación GCM protege los bytes canónicos del documento; un hash no concede
autorización ni demuestra procedencia humana.

No hay campo para IP, tenant, asset_id, reviewer, propiedad o aprobación en el
documento. Los tres UUID de EvidenceContext (organization_id, asset_id, dossier_id)
y version positiva deben venir del servidor, no de un futuro body sin validar.

La gramática cerrada elimina contenedores arbitrarios de secretos, pero no puede
demostrar que un usuario no codifique información en una etiqueta/fecha. Ninguna
evidencia real debe contener secretos. Errores no incluyen el input.

## Envelope versión 1

Objeto JSON limitado a 24 KiB: format_version=1, algorithm=AES-256-GCM+A256KW,
object_id UUID generado por servidor, context, key_id, nonce, wrapped_key,
ciphertext. Binarios en base64 canónico estricto; nonce 12 bytes, wrapped DEK
40 bytes y ciphertext con tag completo. AAD = JSON canónico de TODOS los campos
menos ciphertext. La DEK de 32 bytes es distinta por cifrado.
La cabecera contiene UUID/contexto y key_id en claro como metadatos no secretos;
el documento de evidencia y la DEK/KEK en claro nunca se escriben al filesystem.

open exige object_id/context esperados; no toma autoridad del envelope. Rechaza
contexto cruzado, modificación, truncado, tamaños inválidos, falta/error de llave,
wrapper inválido y plaintext no canónico antes de devolver LabAttestation.
No hay descifrado parcial ni salida de bytes sin validar. El código no registra
contenido, llaves, rutas ni parámetros criptográficos en logs.

ObjectReceipt conserva object_id, contexto esperado y SHA-256 del envelope;
PreparedObject conserva ese recibo y bytes CIFRADOS en memoria. No son tokens de
autorización ni referencias durables de PostgreSQL. No reconstruirlos desde un
archivo hostil para saltarse validaciones de acceso.

## Configuración y filesystem

configured_storage se invoca explícitamente con LAB_EVIDENCE_ROOT,
LAB_EVIDENCE_KEY_ROOT y LAB_EVIDENCE_ACTIVE_KEY_ID. No hay activación al importar
FastAPI, montajes nuevos en Compose ni rutas habilitadas. .env.example deja los
tres vacíos; solo describe ubicaciones/ID, nunca contiene llaves.

Los directorios deben existir, ser absolutos y estar separados (ni anidados),
fuera del repository_root confiable, sin componentes '..' ni symlinks. No se
crean automáticamente. En Linux se abre cada componente con O_NOFOLLOW y
O_DIRECTORY, y se conserva el descriptor. Ancestros no deben ser escribibles por
terceros, salvo directorios sticky como /tmp para fixtures. Directorio final:
propietario UID efectivo, sin permisos de grupo/otros (provisionar 0700). Archivos:
regulares, UID efectivo, sin permisos de grupo/otros (0600; KEK puede ser 0400).
O_NONBLOCK permite rechazar FIFOs sin quedarse esperando; hard links de KEK se
rechazan. No tolera un host/root/proceso del mismo UID malicioso.

Windows nativo: UnsafeStorage antes de abrir almacenamiento. No se afirma prueba
de ACL/NTFS, reparse-point races ni durabilidad Windows. El recorrido de datos
soportado es Linux, usando filesystem local y volumen Linux con hard links/flock/
fsync funcionales. NFS/SMB, bind mounts Windows, múltiples hosts y contenedores
con distintos volúmenes no están calificados. No hay fallback inseguro.

## Escritura y garantías de durabilidad

prepare genera UUID4 y envelope cifrado en memoria. write valida ese envelope
antes de tocar disco, y toma flock exclusivo del directorio; no hay locks SQL.
Cada adquisición abre un descriptor distinto para serializar también threads.

1. Crear stage-<uuid.hex>.tmp con O_CREAT|O_EXCL y modo 0600.
2. Escribir únicamente ciphertext completo; fsync del archivo; fsync directorio.
3. Publicar <uuid.hex>.evidence con hard link exclusivo; nunca sobreescribir.
4. fsync directorio; retirar el link temporal; fsync directorio otra vez.
5. Retornar recibo. El contexto gestor cierra storage y proveedor de llaves.

read toma flock compartido, valida archivo privado, tamaño, hash esperado, AEAD y
documento. Se permiten dos links transitorios durante recuperación de publicación.
El descriptor de directorio queda fijado aunque otro proceso renombre la ruta.

Un error de fsync/publicación produce StorageInterrupted, no éxito. Tras publicar,
el resultado puede ser incierto: conservar el recibo preparado y consultar recover.
No generar otro objeto y asumir que el primero no existe. Sync depende del kernel,
filesystem y dispositivo; las pruebas simulan fallos y muerte de proceso, NO un
corte de energía ni durabilidad del disco físico. tmpfs prueba semántica de proceso,
no conservación después de apagar el host.

## Recuperación local deliberadamente limitada

| Estado al recuperar un recibo conocido | Acción |
| --- | --- |
| Sin final ni temporal | missing; no se crea nada |
| Temporal solo, completo o incompleto | temporary_only; no promover ni borrar |
| Final válido | published; validar bytes/contexto y sincronizar |
| Final válido + temporal con mismo device/inode | retirar SOLO el link temporal redundante; sincronizar |
| Final corrupto o temporal de otro inode | fallar y conservar; no borrar el final |

discard_temporary es aborto EXPLÍCITO de una operación cuyo recibo pertenece al
llamador confiable. Usa el mismo lock y rechaza symlink, archivo no privado o
cualquier final presente. Permite retirar un temporal truncado de esa operación;
no recibe paths y no tiene criterio de antigüedad. Si no hay temporal devuelve false.

Los temporales se reconocen por stage-<32 hex>.tmp, pero el nombre/edad por sí solos
no autorizan destrucción. Tras pérdida del recibo en una caída, conservar los
archivos para reconciliación posterior; no existe GC global ni promoción automática.
No se interpreta published como DB commit, aprobación humana o evidencia aceptada.

Pendiente: reservas persistentes, cuota, fencing, referencias tenant, transacciones
de audit/outbox/replay, reconciliación DB/filesystem, retención, tombstones, backups,
restore y rotación de objetos coordinada. No hay atomicidad distribuida.

## Exclusiones y pruebas

Datos fuera del checkout como control primario. .gitignore, backend/.dockerignore y
el predicado de paquete excluyen *.kek, *.evidence, .evidence.lock, stage-*.tmp y
directorios evidence-data/evidence-storage/evidence-keys. El paquete mantiene guardas
de rama/ancestría y exclusiones anteriores. El predicado se prueba SIN crear ZIP.
No hay garantía contra un operador que copie y renombre un secreto como código;
secret scan complementa pero no sustituye revisión/custodia.

storage_tests está separado del conftest PostgreSQL porque las primitivas no deben
depender de DB. Está incluido en testpaths, Ruff y la imagen quality. El gate
completo sigue ejecutando también todas las pruebas PostgreSQL y cobertura de
ramas >=90%. No se reducen umbrales para pruebas dirigidas.

Comandos de aceptación:

```powershell
.\scripts\backend-quality.ps1
.\scripts\test-package-file-policy.ps1
git diff --check
py scripts\secret_scan.py .
```

Windows portable: usar un venv efímero con cryptography/pydantic/pytest fijados;
desde backend ejecutar solo storage_tests/test_contract_crypto.py. Esto prueba
contrato, crypto y rechazo del adapter Windows, NO escritura nativa en Windows.
Sin bytecode/caché en repo; fixtures TemporaryDirectory verifican su eliminación.
