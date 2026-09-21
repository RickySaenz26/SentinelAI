import type { StatusKind } from '../../components/ui/StatusBadge'

export interface Asset {
  id: string
  ip: string
  hostname: string
  os: string
  ports: number[]
  services: string[]
  status: StatusKind
  lastScan: string
}