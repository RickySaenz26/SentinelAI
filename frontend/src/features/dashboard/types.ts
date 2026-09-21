export type MetricTone = 'neutral' | 'critical' | 'high' | 'success' | 'info'

export interface DashboardMetric {
  id: string
  label: string
  value: number | string
  tone: MetricTone
  hint?: string
}