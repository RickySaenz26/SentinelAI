export type Priority = 'urgent' | 'high' | 'moderate' | 'low'

export interface AiInsight {
  id: string
  title: string
  priority: Priority
  relatedCves: string[]
  technicalExplanation: string
  recommendation: string
}

export interface ExecutiveSummary {
  generatedAt: string
  overallRiskLevel: string
  summary: string
}