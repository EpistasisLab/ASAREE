import { Navigate, useSearchParams } from 'react-router-dom'

// Preserve bookmarks to the former standalone statistics page.
export function StatisticsPage() {
  const [params] = useSearchParams()
  const experiment = params.get('experiment')
  return <Navigate replace to={experiment
    ? `/experiments/${encodeURIComponent(experiment)}/protocol?tab=results&results=statistics`
    : '/experiments'} />
}
