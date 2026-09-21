import type { ReactNode, ReactElement, SVGProps } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

type IconProps = SVGProps<SVGSVGElement>

function IconWrapper({ children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  )
}

const DashboardIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <rect x="3" y="3" width="7" height="9" rx="1" />
    <rect x="14" y="3" width="7" height="5" rx="1" />
    <rect x="14" y="12" width="7" height="9" rx="1" />
    <rect x="3" y="16" width="7" height="5" rx="1" />
  </IconWrapper>
)

const ServerIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <rect x="2" y="3" width="20" height="7" rx="1" />
    <rect x="2" y="14" width="20" height="7" rx="1" />
    <circle cx="6" cy="6.5" r="0.5" fill="currentColor" />
    <circle cx="6" cy="17.5" r="0.5" fill="currentColor" />
  </IconWrapper>
)

const ScanIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <path d="M3 7V5a2 2 0 0 1 2-2h2" />
    <path d="M17 3h2a2 2 0 0 1 2 2v2" />
    <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
    <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
    <line x1="3" y1="12" x2="21" y2="12" />
  </IconWrapper>
)

const ShieldIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <path d="M12 2 4 5v6c0 5 3.4 8.5 8 11 4.6-2.5 8-6 8-11V5l-8-3Z" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </IconWrapper>
)

const BrainIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <circle cx="12" cy="12" r="9" />
    <path d="M8 12a4 4 0 0 1 4-4" />
    <path d="M16 12a4 4 0 0 1-4 4" />
  </IconWrapper>
)

const ReportIcon = (props: IconProps) => (
  <IconWrapper {...props}>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
    <path d="M14 2v6h6" />
    <line x1="8" y1="13" x2="16" y2="13" />
    <line x1="8" y1="17" x2="16" y2="17" />
  </IconWrapper>
)

interface NavItem {
  label: string
  path: string
  icon: (props: IconProps) => ReactElement
}

const navItems: NavItem[] = [
  { label: 'Dashboard', path: '/', icon: DashboardIcon },
  { label: 'Assets', path: '/assets', icon: ServerIcon },
  { label: 'Scans', path: '/scans', icon: ScanIcon },
  { label: 'Vulnerabilities', path: '/vulnerabilities', icon: ShieldIcon },
  { label: 'AI Analysis', path: '/ai-analysis', icon: BrainIcon },
  { label: 'Reports', path: '/reports', icon: ReportIcon },
]

export function AppShell() {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex">
      <aside className="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col">
        <div className="h-16 flex items-center gap-2 px-6 border-b border-slate-800">
          <ShieldIcon className="w-6 h-6 text-cyan-400" />
          <span className="font-semibold text-slate-50 tracking-tight">
            SentinelAI <span className="text-cyan-400">Security</span>
          </span>
        </div>

        <nav className="flex-1 px-3 py-4 space-y-1">
          {navItems.map(({ label, path, icon: Icon }) => {
            const active = location.pathname === path
            return (
              <Link
                key={label}
                to={path}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  active
                    ? 'bg-cyan-500/10 text-cyan-400'
                    : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100'
                }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            )
          })}
        </nav>
      </aside>

      <div className="flex-1 flex flex-col">
        <header className="h-16 border-b border-slate-800 flex items-center px-6">
          <h1 className="text-sm font-medium text-slate-300">Security Overview</h1>
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
