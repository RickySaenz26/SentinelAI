import type { DashboardMetric } from './types'

export const dashboardMetrics: DashboardMetric[] = [
  {
    id: 'assets',
    label: 'Assets Discovered',
    value: 47,
    tone: 'info',
    hint: '3 new this week',
  },
  {
    id: 'critical',
    label: 'Critical Vulnerabilities',
    value: 5,
    tone: 'critical',
    hint: 'Requires immediate attention',
  },
  {
    id: 'high',
    label: 'High Vulnerabilities',
    value: 12,
    tone: 'high',
    hint: '2 with known exploits',
  },
  {
    id: 'risk',
    label: 'Overall Risk Score',
    value: '7.2',
    tone: 'high',
    hint: 'Out of 10',
  },
  {
    id: 'audits',
    label: 'Recent Audits',
    value: 3,
    tone: 'neutral',
    hint: 'Last 30 days',
  },
  {
    id: 'scans',
    label: 'Scans Completed',
    value: 18,
    tone: 'success',
    hint: 'This month',
  },
]