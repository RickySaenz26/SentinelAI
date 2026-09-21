export type ReportStatus = 'draft' | 'final'
export type RiskLevel = 'critical' | 'elevated' | 'moderate' | 'low'

export interface Report {
  id: string
  title: string
  createdAt: string
  scope: string
  riskLevel: RiskLevel
  status: ReportStatus
  findingsCount: number
}