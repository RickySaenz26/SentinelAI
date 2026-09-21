export type Severity = 'critical' | 'high' | 'medium' | 'low'
export type VulnerabilityStatus = 'open' | 'in_progress' | 'resolved' | 'accepted_risk'

export interface Vulnerability {
  id: string
  cve: string
  service: string
  asset: string
  severity: Severity
  cvss: number
  exploitAvailable: boolean
  evidence: string
  source: string
  recommendation: string
  status: VulnerabilityStatus
}