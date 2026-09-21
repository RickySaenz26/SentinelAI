export type ScanType = 'quick' | 'standard' | 'deep'
export type ScanStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface ScanTypeDefinition {
  id: ScanType
  label: string
  description: string
  command: string
  duration: string
}

export interface ScanJob {
  id: string
  target: string
  type: ScanType
  status: ScanStatus
  progress: number
  startedAt: string
  findings: number | null
}