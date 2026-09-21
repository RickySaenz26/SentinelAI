import type { AiInsight, ExecutiveSummary } from './types'

export const executiveSummary: ExecutiveSummary = {
  generatedAt: '2026-09-13 11:20',
  overallRiskLevel: 'Elevated',
  summary:
    'La infraestructura analizada presenta 1 vulnerabilidad crítica con exploit público disponible (CVE-2024-6387) en un servidor de producción expuesto a internet. Se detectó además una posible cadena de ataque combinando dos vulnerabilidades de severidad alta en el mismo activo. Se recomienda priorizar el parcheo de OpenSSH en web-prod-01 dentro de las próximas 24-48 horas.',
}

export const aiInsights: AiInsight[] = [
  {
    id: 'i1',
    title: 'Cadena de ataque potencial en web-prod-01',
    priority: 'urgent',
    relatedCves: ['CVE-2024-6387', 'CVE-2023-44487'],
    technicalExplanation:
      'Un atacante podría explotar CVE-2024-6387 (RCE en OpenSSH) para obtener acceso inicial, y posteriormente usar CVE-2023-44487 para escalar el impacto mediante ataques de denegación de servicio sobre el servidor web, ambos en el mismo activo.',
    recommendation:
      'Priorizar el parcheo de OpenSSH antes que cualquier otra vulnerabilidad de este activo, dado que es el vector de entrada de la cadena.',
  },
  {
    id: 'i2',
    title: 'Base de datos con cifrado desactualizado',
    priority: 'high',
    relatedCves: ['CVE-2022-1292'],
    technicalExplanation:
      'db-prod-01 expone una versión de OpenSSL vulnerable en su capa de conexión TLS. Aunque no hay exploit público conocido, el CVSS de 9.8 indica severidad crítica si se llegara a explotar.',
    recommendation:
      'Actualizar OpenSSL en el próximo ciclo de mantenimiento programado, sin esperar a que aparezca un exploit público.',
  },
  {
    id: 'i3',
    title: 'Activo legacy con riesgo aceptado requiere revisión',
    priority: 'moderate',
    relatedCves: ['CVE-2021-41773'],
    technicalExplanation:
      'legacy-app-01 mantiene una vulnerabilidad de severidad media marcada como "riesgo aceptado". Dado que es un sistema legacy con menor visibilidad, se recomienda re-evaluar periódicamente esta decisión.',
    recommendation:
      'Revisar en el próximo comité de seguridad si el riesgo aceptado sigue siendo válido o si el sistema debe ser reemplazado.',
  },
]