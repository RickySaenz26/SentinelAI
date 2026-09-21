# Política de seguridad

## Versiones compatibles

SentinelAI está en desarrollo pre-1.0. Solo la rama principal vigente recibe
correcciones de seguridad.

## Reporte responsable

No abras un issue público con una vulnerabilidad, credencial o dato de cliente.
Usa un **Security Advisory privado de GitHub** en el repositorio. Incluye:

- componente y versión o commit afectado;
- pasos mínimos para reproducir;
- impacto esperado;
- evidencia redactada, sin secretos reales;
- mitigación sugerida, si existe.

## Tratamiento inicial

El responsable del repositorio debe confirmar recepción, clasificar severidad,
preparar una corrección en privado y publicar el aviso cuando la mitigación esté
disponible. Los plazos formales se definirán antes de la primera versión pública.

## Secretos y datos sensibles

- Nunca versionar `.env`, llaves privadas, tokens o códigos de recuperación.
- Rotar inmediatamente cualquier secreto que haya entrado en un ZIP o commit.
- Ejecutar `python scripts/secret_scan.py .` antes de compartir un paquete.
- No usar datos reales de clientes en desarrollo, pruebas o demostraciones.

El escáner incluido detecta patrones de alta confianza, pero no garantiza la
ausencia total de secretos.
