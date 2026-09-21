# Contribuir a SentinelAI Security

## Flujo de trabajo

1. Crear una rama desde `main` o `develop`.
2. Mantener los cambios pequeños y enfocados.
3. No incluir secretos, datos de clientes ni resultados reales de escaneos.
4. Ejecutar `.\scripts\verify.ps1` desde Windows PowerShell.
5. Abrir un pull request con objetivo, riesgos, pruebas y capturas cuando cambie UI.

## Convenciones

- Commits convencionales: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.
- Python: Ruff, Python 3.12 y cobertura mínima del 90 %.
- Frontend: TypeScript estricto, ESLint y compilación Vite obligatoria.
- Las rutas públicas deben conservar compatibilidad o documentar la ruptura.

## Criterios de aceptación

- CI en verde.
- Sin secretos ni vulnerabilidades conocidas de severidad alta en dependencias.
- Pruebas nuevas para cambios de backend.
- Documentación actualizada cuando cambien comandos o configuración.

Los hallazgos de seguridad se reportan según [SECURITY.md](./SECURITY.md), no en
issues públicos.
