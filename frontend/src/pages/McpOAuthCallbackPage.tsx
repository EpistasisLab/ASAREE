import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AuthLayout } from '@/components/AuthLayout'
import { postMcpOAuthResult } from '@/lib/mcpOAuth'

export function McpOAuthCallbackPage() {
  const [params] = useSearchParams()
  const succeeded = params.get('status') === 'success'

  useEffect(() => {
    postMcpOAuthResult(params)
    const timer = window.setTimeout(() => window.close(), 150)
    return () => window.clearTimeout(timer)
  }, [params])

  return (
    <AuthLayout
      title={succeeded ? 'MCP server authorized' : 'Authorization failed'}
      description={succeeded ? 'Returning to ASAREE…' : 'Return to ASAREE and try connecting again.'}
    >
      <p className="text-center text-sm text-muted-foreground">You can close this window.</p>
    </AuthLayout>
  )
}
