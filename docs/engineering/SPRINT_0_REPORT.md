# Sprint nocturno 0 — informe de estabilización

Fecha: 2026-09-17

## Objetivo

Estabilizar la base existente sin añadir alcance funcional. Las pantallas siguen
utilizando datos de demostración y no se implementaron autenticación, base de
datos, Nmap, Searchsploit, generación de reportes ni integración con LLM.

## Cambios realizados

- Corregido el fallo TypeScript `TS2503` mediante tipos importados de React.
- Añadidos comandos `typecheck` y `check` al frontend.
- Fijadas las dependencias directas de ejecución y desarrollo del backend.
- Modernizada la configuración con `SettingsConfigDict` y normalización de CORS.
- Añadida factoría `create_app`, logging JSON y correlación `X-Request-ID`.
- Conservado `/api/v1/health` y añadidos `/health/live` y `/health/ready`.
- Añadidas pruebas de contrato, configuración y rutas con cobertura mínima de 90 %.
- Añadidos Ruff, pytest, cobertura y auditoría de dependencias.
- Añadidos flujos CI, auditoría programada y Dependabot.
- Añadido escaneo local de patrones de secretos de alta confianza.
- Añadidos scripts PowerShell de verificación y ejecución local.
- Actualizada la documentación raíz de seguridad, contribución y estado.

## Resultados de aceptación

Controles ejecutados sobre la entrega final:

| Control | Resultado |
| --- | --- |
| `pnpm lint` | Aprobado |
| TypeScript + `vite build` | Aprobado; 103 módulos transformados |
| `pnpm audit --prod --audit-level=high` | 0 vulnerabilidades conocidas |
| `ruff format --check` | Aprobado |
| `ruff check` | Aprobado |
| `pytest` | 5 pruebas aprobadas |
| Cobertura del backend | 95,33 %; umbral mínimo 90 % |
| `pip-audit -r requirements.txt` | 0 vulnerabilidades conocidas |
| `scripts/secret_scan.py` | Sin patrones de alta confianza detectados |
| Workflows YAML | 2 archivos válidos, 5 jobs en total |

La suite muestra una advertencia de deprecación interna de Starlette/AnyIO; no
afecta el resultado y debe revisarse al actualizar esas dependencias. Los scripts
PowerShell fueron revisados estáticamente; deben ejecutarse en Windows 11 para
validar el entorno local específico.

Consultar `SPRINT_0_COMMANDS_POWERSHELL.md` para repetir todos los controles.

## Decisiones aplazadas deliberadamente

- Modelo de identidad, sesión, RBAC y multiempresa.
- PostgreSQL, SQLAlchemy, Alembic y modelo de datos.
- Cola de trabajos, Redis y aislamiento de scanners.
- Ejecución real de herramientas ofensivas o de enumeración.
- Selección e integración de modelos locales.
- Licencia comercial/open source definitiva.

Estas decisiones pertenecen al Backend v1 y deben revisarse antes de convertir
el scaffold en funcionalidad de producción.
