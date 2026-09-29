import { mcpServersApi } from '@/api/client'

const MESSAGE_TYPE = 'asaree:mcp-oauth'

type OAuthMessage = {
  type: typeof MESSAGE_TYPE
  status: 'success' | 'error'
  serverId?: string
  code?: string
}

export async function authorizeMcpServer(serverId: string, scope?: string): Promise<void> {
  const popup = window.open('', 'asaree-mcp-oauth', 'popup,width=640,height=760')
  if (!popup) throw new Error('Allow popups to authorize this MCP server.')
  const oauthWindow = popup

  let authorization
  try {
    authorization = await mcpServersApi.beginOAuth(serverId, scope)
  } catch (error) {
    oauthWindow.close()
    throw error
  }
  oauthWindow.location.assign(authorization.authorization_url)

  await new Promise<void>((resolve, reject) => {
    const remaining = Math.max(1_000, new Date(authorization.expires_at).getTime() - Date.now())
    const timeout = window.setTimeout(() => finish(new Error('MCP authorization timed out.')), remaining)
    const closed = window.setInterval(() => {
      if (oauthWindow.closed) finish(new Error('MCP authorization was cancelled.'))
    }, 500)

    function finish(error?: Error) {
      window.clearTimeout(timeout)
      window.clearInterval(closed)
      window.removeEventListener('message', onMessage)
      if (!oauthWindow.closed) oauthWindow.close()
      if (error) reject(error)
      else resolve()
    }

    function onMessage(event: MessageEvent<OAuthMessage>) {
      if (event.origin !== window.location.origin || event.source !== oauthWindow || event.data?.type !== MESSAGE_TYPE) return
      if (event.data.status === 'success' && event.data.serverId === serverId) finish()
      else finish(new Error(oauthErrorMessage(event.data.code)))
    }

    window.addEventListener('message', onMessage)
  })
}

export function postMcpOAuthResult(params: URLSearchParams): void {
  const status = params.get('status') === 'success' ? 'success' : 'error'
  window.opener?.postMessage(
    {
      type: MESSAGE_TYPE,
      status,
      serverId: params.get('server_id') ?? undefined,
      code: params.get('code') ?? undefined,
    } satisfies OAuthMessage,
    window.location.origin,
  )
}

function oauthErrorMessage(code?: string): string {
  if (code === 'authorization_rejected') return 'Authorization was rejected by the provider.'
  if (code === 'invalid_state') return 'The authorization request expired or was already used.'
  if (code === 'token_exchange_failed') return 'The provider rejected the authorization code.'
  return 'MCP authorization failed.'
}
