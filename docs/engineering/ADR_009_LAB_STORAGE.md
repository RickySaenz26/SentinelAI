# ADR-009 — especialización de laboratorio, incremento 2 etapa 1

Fecha: 2026-09-30 (America/Lima). Aprobación del usuario limitada al laboratorio,
según el prompt de esta etapa. No aprueba globalmente ADR-009/DP-04, retención
institucional, producción ni redes de SENATI. La especificación original conserva
su carácter propuesto fuera de esta especialización.

## Decisiones aprobadas y frontera

- Storage local privado dedicado, llaves separadas y ambos fuera del repositorio.
- Evidencia estructurada de control técnico, vinculada por asset_id confiable.
  La IPv4 se introduce manualmente al inventariar y la valida la política del
  operador. El documento NO admite IP/target alternativo ni campos de autoridad.
- ownership_status sigue unverified. Control técnico no acredita propiedad legal
  ni concede autorización para escanear.
- Presentador y revisor deben ser personas/usuarios distintos; platform_admin no
  tendrá bypass. El workflow y esa autorización NO se implementan en esta etapa.
- Máximo futuro de verificación: 30 días; conservación de evidencia: 90 días.
  Estos plazos están aprobados para laboratorio, pero aún NO se ejecutan mediante
  DB, endpoints o procesos de retención.
- Retirar una IP invalida el uso de la revisión anterior. Readmitirla exige una
  NUEVA revisión humana, nunca reactivación automática. Esta decisión del usuario
  sustituye la suspensión reversible propuesta en el plan del chat. La siguiente
  etapa deberá persistir invalidación/generación de política; comparar únicamente
  la allowlist actual no detecta una retirada/readmisión entre reinicios.
- Ventana propuesta de backups: 7 días, pendiente de implementación, operación y
  restore. No existen backups o custodio nominal acreditados por este cambio.

## Implementado ahora

Contrato lab_control_attestation_v1, canonicalización, puertos KeyProvider y
EvidenceStorage, proveedor de KEK por archivos preaprovisionados, envelope
AES-256-GCM/AES-KW, storage Linux exclusivo, recibos internos y recuperación local
conservadora. Sin acceso a PostgreSQL ni red, sin endpoints ni migraciones.

El filesystem se abstrae; no se añade BYTEA para blobs ni uploads arbitrarios.
Los IDs de tenant/activo/expediente/versión y objeto están autenticados por AEAD.
Un contexto válido sintácticamente NO es prueba de autorización. Solo la futura
aplicación autenticada podrá construirlo después de validar ActorContext y DB.

## Alternativas y motivos

- No se utiliza filesystem Windows nativo: comprobaciones pathlib/lstat no
  sustituyen apertura segura con handles, ACL y protección de reparse points.
  El adaptador rehúsa ejecutarse fuera de Linux; contrato/crypto sí son portables.
  Para la laptop Windows se requiere el contenedor Linux y un volumen Linux, no
  un bind mount de una carpeta Windows para datos o llaves.
- No se usa os.replace/rename con sobreescritura: se publica mediante hard link
  exclusivo dentro del mismo directorio/filesystem. Un objeto existente nunca se
  reemplaza, incluso si es un symlink o proviene de otra operación.
- No se borra un objeto final por antigüedad, nombre ni ausencia aparente de una
  referencia DB: aún no hay coordinación DB/storage. No se promueven temporales.
- No se incorporan workers, publisher, escáner ni verificación automática.

## Llaves y rotación

cryptography==50.0.2 (PyCA), comprobado compatible con Python 3.12 Linux y 3.14
Windows mediante pruebas. AESGCM con DEK aleatoria de 256 bits por escritura,
nonce aleatorio de 96 bits, tag completo de 128 bits; aes_key_wrap con KEK de
256 bits (RFC 3394). No se implementan cifradores ni autenticadores propios.

KEK: archivo binario exactamente 32 bytes, nombre key_id.kek, directorio separado,
permisos privados. Nunca autogenerada por código productivo ni en .env. Los tests
generan llaves aleatorias solo como fixtures y destruyen sus directorios.

Cambiar active_key_id selecciona otra KEK para objetos nuevos; el lector usa la
key_id del envelope validado y permite coexistencia de llaves antiguas. La key_id
y el wrapped_key también están en AAD: modificarlos invalida el tag, incluso si
dos IDs apuntaran a la misma KEK. Por ello este formato NO permite reemplazar solo
el envoltorio manteniendo el tag original. Una futura migración de llave deberá
crear otro objeto/envelope autenticado (sin sobrescribir el anterior), verificar
el mismo documento y coordinar el cambio de referencia con DB. No se implementa
todavía rewrap en sitio ni se declara completada la rotación operacional.

La propuesta de rotación cada 90 días/inmediata por incidente sigue siendo un
runbook pendiente. Retirar llaves requiere inventario de referencias y backups.
Eliminar una KEK compartida no es un mecanismo de borrado individual de evidencia.

## Requisitos de puesta en operación, no satisfechos por fixtures

Custodio identificado, directorios/ACL aprovisionados y revisados, volumen Linux
local con garantías de sync, gestión y respaldo separado de llaves, restore
probado, política de retención operativa y coordinación transaccional DB/storage.
El runtime confiable y el administrador del host pueden acceder a memoria/llaves;
este diseño no protege contra su compromiso ni promete zeroization de Python.

Referencias oficiales consultadas al implementar:
- https://cryptography.io/en/stable/hazmat/primitives/aead/
- https://cryptography.io/en/stable/hazmat/primitives/keywrap/
- https://cryptography.io/en/stable/installation/

Contrato y recuperación: [guía de etapa 1](SPRINT_2A_INCREMENT_2_STAGE_1.md).
