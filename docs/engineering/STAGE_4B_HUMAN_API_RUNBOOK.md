# 4B: revisión humana API-first en laboratorio

Esta guía no es un acta de aceptación. Los smokes usan cuentas sintéticas; no se ha
observado aquí una revisión por dos personas. Control técnico no demuestra propiedad
legal ni autoriza escaneo. `ownership_status` permanece `unverified`.

## Preparación del operador

Usar exclusivamente un despliegue de laboratorio Linux con HTTPS, PostgreSQL y
migraciones hasta 09. No ejecutar contra servicios operativos ni SENATI. La política
4A debe publicarse por el canal restringido existente, incluyendo expresamente el
IPv4 exacto del laboratorio. Una política ausente/vacía deniega; una red privada no
es consentimiento. No usar las fixtures como configuración habilitada por defecto.

Configurar storage privado y llaves fuera de Git, imagen y paquete siguiendo las
etapas 1–3. No copiar llaves, cookies, contraseñas o contenido de evidencia al informe.
Para una aceptación humana, designar persona A (presentadora) y persona B (revisora)
con cuentas diferentes y roles permitidos. El sistema comprueba `user_id`, no que
las cuentas pertenezcan realmente a personas distintas.
Para reproducir toda esta secuencia, A puede ser `org_owner` y B
`security_manager` del mismo tenant. Si A es `analyst`, un owner/manager debe
registrar previamente el activo: el permiso de presentar no concede crear activos.
El contrato completo está congelado en
`backend/tests/contracts/control.openapi.json`; el de evidencia de etapa 3 está en
`backend/tests/contracts/evidence.openapi.json`.

Cada persona inicia `POST /api/v1/session` con su propia cuenta y mantiene un cookie
jar separado. Conservar la cookie `__Host-sentinel_session` solo en ese cliente.
Tomar `csrf_token` de la respuesta y usar el nombre configurado por
`CSRF_HEADER_NAME` (por defecto `X-CSRF-Token`) en cada mutación. Si se envía
`Origin`, debe estar en trusted origins; no desactivar esa comprobación.

## Secuencia observable

1. A registra o consulta un activo permitido (`/api/v1/assets`). Anota su UUID y
   versión. Nunca suministra otro tenant, IP alternativa ni identidad en la revisión.
2. A presenta evidencia estructurada propia mediante
   `POST /api/v1/evidence/assets/{asset_id}` con el contrato cerrado de etapa 3.
   Esa evidencia debe reflejar una observación local real en la aceptación humana,
   no una fixture. Obtiene `id` y `version`, sin incluir documentos arbitrarios.
3. A llama `POST /api/v1/assets/{asset_id}/control-reviews` con CSRF,
   `If-Match: <versión-del-activo>`, `Idempotency-Key: <clave-nueva>` y:

   ```json
   {"evidence_id":"<UUID confirmado>","evidence_version":1}
   ```

   Esperado: 201, `state=pending`, versión 1 y referencias inmutables. Un 409 no
   autoriza a omitir If-Match: consultar el estado y revisar el conflicto.
4. B consulta `GET /api/v1/control-reviews/{review_id}` y lee realmente
   `GET /api/v1/evidence/assets/{asset_id}/{evidence_id}/content` **después** de la
   solicitud. Revisa esa versión y sus observaciones; no aprobar a ciegas. El servidor
   vincula el evento auditado de lectura de B. Descargar contenido no certifica su
   verdad ni demuestra comprensión humana.
5. B decide con `POST /api/v1/control-reviews/{review_id}/decisions`, su CSRF,
   `If-Match: <versión-de-la-revisión>` y otra clave de idempotencia:

   ```json
   {
     "decision":"approved",
     "checklist_version":1,
     "checklist":{
       "console_identity":"confirmed",
       "inventory_match":"confirmed",
       "administrative_control":"confirmed"
     },
     "reason_code":"control_confirmed"
   }
   ```

   Si no puede confirmar algún punto, no marcarlo como confirmado. Usar `rejected`
   con resultados `not_confirmed`/`not_assessable` y razón de catálogo
   `mismatch`, `incomplete` o `not_assessable`. Sin texto libre ni adjuntos.
6. Consultar `GET /api/v1/assets/{asset_id}/control-status`. Solo una respuesta
   actual con `valid=true`, `checked_at` y sin razones negativas informa vigencia
   técnica comprobada en ese instante. No inferirla de `state=approved` histórico
   ni del replay. La consulta vuelve a leer/descifrar storage y revalida autoridad.
7. Para renovar, A crea otra solicitud con `renews_review_id` apuntando a la
   aprobación anterior. Reutilizar evidencia retenida no reinicia sus 90 días.
   B debe leer después de la nueva solicitud y emitir otra decisión. Máximo 30 días
   desde cada decisión, acotado por la retención original.
8. Solo A retira una pendiente: POST `.../{review_id}/withdrawals`, razón
   `presenter_withdrawal`. Owner/manager puede revocar una aprobación, incluso propia:
   POST `.../{review_id}/revocations`, razón `confidence_withdrawn` o `error_found`.
   Ambas exigen versión de revisión y nueva clave. La aprobación sustituida nunca
   vuelve a ser vigente al revocar otra posterior.

En todas las rutas anteriores los sufijos de mutación completos comienzan por
`/api/v1/control-reviews/`. Las respuestas no se deben almacenar en caches.
Viewer/platform_admin solo reciben el resumen mínimo en
`GET /api/v1/assets/{asset_id}/control-summary`.

## Errores y prueba manual negativa

- Autoaprobar, incluso con otra sesión de A: 409 `CONTROL_SEPARATION_REQUIRED`.
- B decide sin lectura posterior: 409 `CONTROL_READING_REQUIRED`.
- Versión desactualizada: 409 `VERSION_CONFLICT`; no repetir ciegamente con otro número.
- Sesión/CSRF/rol inválidos: 401/403. Recurso fuera del tenant o analista ajeno: 404.
- 503: la confirmación puede ser incierta. No asumir aprobación ni duplicar la
  intención con otra clave: reintentar la misma clave, payload, ruta e If-Match
  durante las 24 horas del replay, después de reautenticar. Luego consultar vigencia.
- Evidencia fuera de retención: 410 al presentar; ilegible/no verificable: denegación
  o status `valid=false`, nunca una respuesta positiva cacheada.
- Retirar/readmitir la IP con el mismo hash de política cambia la generación:
  no reactiva solicitudes ni aprobaciones. Archivo invalida; nombre/criticidad no.

Para cerrar una aceptación humana, registrar por separado quiénes participaron
(según el proceso del operador), fecha, versión Git/image, IDs de solicitud/eventos,
resultados esperados/observados y limitaciones; no guardar evidencia descifrada ni
credenciales. La API atribuye el juicio a cuentas; ese registro requiere una
observación humana externa y no lo genera automáticamente el smoke.
