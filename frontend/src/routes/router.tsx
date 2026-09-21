import { createBrowserRouter } from 'react-router'
import { AppShell } from '../components/layout/AppShell'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { AssetsPage } from '../features/assets/AssetsPage'
import { ScansPage } from '../features/scans/ScansPage'
import { VulnerabilitiesPage } from '../features/vulnerabilities/VulnerabilitiesPage'
import { AiAnalysisPage } from '../features/ai-analysis/AiAnalysisPage'
import { ReportsPage } from '../features/reports/ReportsPage'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <DashboardPage /> },
      { path: '/assets', element: <AssetsPage /> },
      { path: '/scans', element: <ScansPage /> },
      { path: '/vulnerabilities', element: <VulnerabilitiesPage /> },
      { path: '/ai-analysis', element: <AiAnalysisPage /> },
      { path: '/reports', element: <ReportsPage /> },
    ],
  },
])