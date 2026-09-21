import type { Report } from './types'

export const reports: Report[] = [
  {
    id: 'r1',
    title: 'Auditoría Trimestral Q3 2026',
    createdAt: '2026-09-13',
    scope: '5 assets — infraestructura completa',
    riskLevel: 'elevated',
    status: 'final',
    findingsCount: 5,
  },
  {
    id: 'r2',
    title: 'Evaluación de Seguridad — web-prod-01',
    createdAt: '2026-09-10',
    scope: '1 asset — servidor web de producción',
    riskLevel: 'critical',
    status: 'final',
    findingsCount: 2,
  },
  {
    id: 'r3',
    title: 'Auditoría de Infraestructura Legacy',
    createdAt: '2026-08-28',
    scope: '1 asset — legacy-app-01',
    riskLevel: 'moderate',
    status: 'draft',
    findingsCount: 1,
  },
]