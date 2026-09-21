import type { ScanTypeDefinition, ScanJob } from './types'

export const scanTypes: ScanTypeDefinition[] = [
  {
    id: 'quick',
    label: 'Quick Scan',
    description: 'Detección rápida de servicios y versiones en puertos comunes.',
    command: 'nmap -sV',
    duration: '~2 min',
  },
  {
    id: 'standard',
    label: 'Standard Scan',
    description: 'Escaneo balanceado con detección de SO y scripts básicos.',
    command: 'nmap -sV -sC -O',
    duration: '~8 min',
  },
  {
    id: 'deep',
    label: 'Deep Scan',
    description: 'Análisis exhaustivo de todos los puertos y detección de vulnerabilidades.',
    command: 'nmap -sV -sC -O -p- -T4 --script=vuln',
    duration: '~30+ min',
  },
]

export const scanJobs: ScanJob[] = [
  {
    id: 's1',
    target: '10.0.0.12 (web-prod-01)',
    type: 'deep',
    status: 'completed',
    progress: 100,
    startedAt: '2026-09-13 09:12',
    findings: 8,
  },
  {
    id: 's2',
    target: '10.0.0.41 (vpn-gateway)',
    type: 'standard',
    status: 'running',
    progress: 62,
    startedAt: '2026-09-13 10:45',
    findings: null,
  },
  {
    id: 's3',
    target: '10.0.0.20 (mail-01)',
    type: 'quick',
    status: 'queued',
    progress: 0,
    startedAt: '2026-09-13 10:50',
    findings: null,
  },
  {
    id: 's4',
    target: '10.0.0.33 (legacy-app-01)',
    type: 'standard',
    status: 'failed',
    progress: 34,
    startedAt: '2026-09-12 16:20',
    findings: null,
  },
]