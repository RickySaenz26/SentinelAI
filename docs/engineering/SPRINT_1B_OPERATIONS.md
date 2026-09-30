# Operación — Sprint 1B.1 Closure

Repositorio canónico: `C:\SentinelAI\platform`.
Esta guía sustituye las instrucciones antiguas de arranque para el cierre actual.

## Inicio y gates de cierre

```powershell
Set-Location "C:\SentinelAI\platform"
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
docker compose config --quiet
.\scripts\backend-quality.ps1
.\scripts\authenticated-compose-smoke.ps1
```

`backend-quality.ps1` usa PostgreSQL efímero en `tmpfs`; no opera sobre la base
persistente local. `authenticated-compose-smoke.ps1` crea un proyecto Compose
aleatorio, HTTPS con CA efímera, credenciales aleatorias solo en memoria y elimina
sus contenedores, redes y volúmenes al finalizar.

## Operación y credenciales

Solo desarrollo local sin registro público. Frontend con mocks. El proyecto
sentinelai-closure usa puerto 8083 y volúmenes propios. Se detiene con
`docker compose down`, sin eliminar datos.

Bootstrap/onboarding se ejecutan en terminal confiable con entrada oculta de
contraseña, nunca argumentos ni secretos impresos. No se precreó una contraseña
para el usuario. Bootstrap requiere identidad vacía y confirma organización,
primer platform_admin, audit y outbox en una transacción. Onboarding permite solo
org_owner, security_manager, analyst, viewer, auditor. Repetición conflictiva falla
sin cambios parciales.

## Sesiones y CSRF

Cookie __Host-sentinel_session: Secure/HttpOnly/SameSite=Lax/Path=/ sin Domain.
Se guardan hashes, nunca tokens en claro. Login/rotate/switch generan tokens nuevos;
logout/recovery/suspensión revocan sesiones. Idle persistente limitado por expiry
absoluto y revalidado tras locks.

CSRF_HEADER_NAME (default X-CSRF-Token) se resuelve al iniciar proceso. Cambiarlo
requiere reinicio. Mismo valor en CORS/OpenAPI/auth. Toda mutación autenticada
requiere CSRF válido; Origin presente debe estar en TRUSTED_ORIGINS. CORS incluye
If-Match. No existe excepción HTTP insegura: integrar navegador requiere TLS.
Las pruebas unitarias/integración usan HTTPS in-process sin desactivar Secure,
CSRF ni RLS. El smoke de aceptación usa el stack Compose desplegado con HTTPS.
La identidad temporal `platform_admin` usada por ese smoke existe únicamente en
el entorno efímero, se destruye con él, no persiste credenciales operacionales y
no es una recomendación de onboarding para producción.

## Orden de locks

Normal: advisory organización(seed1), fila objetivo/sesión, advisory audit(seed0).
Scope, estado y versión se releen bajo lock. Último owner y onboarding comparten
el orden. If-Match obsoleto: 409 VERSION_CONFLICT sin cambio de versión ni eventos.

Switch y recovery confirm toman primero guard global sentinelai-org-switch(seed3),
antes de org/fila, porque cambian sesiones entre tenants. Evita ciclos A→B/B→A y
revocación multi-org. Bootstrap toma sentinelai-bootstrap(seed2) antes de crear
su tenant; SQL serializa inserts de membresías para autorizar solo la primera
platform_admin. SQL administrativo arbitrario no es una API soportada y requiere
revisión propia de orden/transacciones.

## Outbox e idempotencia

Los endpoints de Sprint 1B no tienen replay ni Idempotency-Key HTTP. If-Match protege PATCH/DELETE:
retry después de éxito devuelve 409, no otro evento. POST duplicado devuelve 409.
Claves internas de 1–128 ASCII seguros, scoped por organización:
org.updated:ID:version; membership.created/updated/revoked:ID:version;
managed_user.created:user:1; session.login:session; session.rotated/logout:oldSession;
organization.activated:oldSession; recovery.requested/completed:recoveryID;
platform.bootstrap:organization. No incluyen contraseñas, tokens ni correo.

Reemitir misma clave/contenido devuelve el evento existente; diferente contenido
produce 409 IDEMPOTENCY_CONFLICT. Mutación/audit/outbox comparten transacción.
La unicidad de outbox no convierte todos los endpoints en idempotentes HTTP.
Sin publisher ni workers.

Sprint 2A incremento 1 añade replay HTTP únicamente a POST/DELETE de activos;
no cambia los contratos de foundation. Véase [política y activos](SPRINT_2A_INCREMENT_1.md).

## RLS, privilegios y confianza

RLS forzado en organizations, roles, memberships, security_audit_events,
outbox_events. app.organization_id/app.user_id son selectores confiados al backend,
no pruebas de identidad criptográficas. SQL inyectado que conozca IDs puede cambiar
GUCs; RLS no reemplaza autorización ni consultas parametrizadas. La defensa de
platform_admin NO depende de una variable app.* controlable por runtime.

users/password_credentials/sessions/account_recovery_tokens son globales por
login/recovery multi-org; permissions/role_permissions son catálogos globales.
No se afirma RLS en esas tablas. Runtime necesita leer identidades/credenciales
y crear/revocar sesiones; comprometer la credencial SQL runtime sigue comprometiendo
la frontera de confianza de la aplicación. Se elimina UPDATE users, se limita
UPDATE a columnas operacionales donde procede y se niega DELETE/CREATE/ownership/
BYPASSRLS. Roles/permisos son solo lectura; audit/outbox SELECT/INSERT sin UPDATE/
DELETE. La matriz de privilegios efectiva se comprueba con SQL en los tests.

## Auditoría y límites residuales

Audit v3 firma event ID, organización, secuencia, timestamp UTC, actor type/user,
acción, recurso, resultado, request ID, detalles y predecessor. Verificador exige
contexto tenant coherente. Igual timestamp no altera el orden. Legacy v1/v2 conserva
hashes y garantías originales: NO protección retroactiva de campos antes no firmados.
Hash chain local no resiste a un administrador DB que reescriba toda la historia;
anclaje externo/retención inmutable pendientes.

Recovery devuelve 202 no enumerativo y nunca imprime/devuelve tokens. Adaptador de
pruebas in-process solo ENVIRONMENT=test; no existe delivery real. Rate limiting
en memoria no es distribuido; se requiere revisar IP/proxy antes de exposición.
Son límites de foundation local, no autorización para producción.
