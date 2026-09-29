import { mcpServersApi } from '@/api/client'
import type { McpServer } from '@/types/mcpServers'

const MESSAGE_TYPE = 'asaree:mcp-oauth'
// Same-origin fallback for providers whose Cross-Origin-Opener-Policy severs
// `window.opener`: the callback page can then no longer postMessage to us, and
// the popup handle reports `closed` while the user is still authorizing.
const CHANNEL_NAME = 'asaree:mcp-oauth'
const STATUS_POLL_MS = 2_000

type OAuthMessage = {
  type: typeof MESSAGE_TYPE
  status: 'success' | 'error'
  serverId?: string
  code?: string
}

export type AuthorizeMcpServerOptions = {
  signal?: AbortSignal
  // The popup handle stopped reporting (closed by the user, or detached by the
  // provider's COOP). Authorization keeps waiting until it completes, expires,
  // or `signal` aborts, so the caller should offer a way to cancel.
  onDetached?: () => void
}

export function isOAuthAuthorized(server: McpServer): boolean {
  const auth = server.authentication
  return auth.auth_mode === 'oauth' && auth.configured && !auth.authorization_required
}

export async function authorizeMcpServer(
  serverId: string,
  scope?: string,
  { signal, onDetached }: AuthorizeMcpServerOptions = {},
): Promise<void> {
  const popup = window.open('', 'asaree-mcp-oauth', 'popup,width=640,height=760')
  if (!popup) throw new Error('Allow popups to authorize this MCP server.')
  const oauthWindow = popup

  let authorization
  let alreadyAuthorized
  try {
    // Status polling can only detect a *change* to authorized; a server whose
    // existing tokens are still valid must wait for the callback message.
    alreadyAuthorized = isOAuthAuthorized(await mcpServersApi.get(serverId))
    authorization = await mcpServersApi.beginOAuth(serverId, scope)
  } catch (error) {
    oauthWindow.close()
    throw error
  }
  if (signal?.aborted) {
    oauthWindow.close()
    throw new Error('MCP authorization was cancelled.')
  }
  oauthWindow.location.assign(authorization.authorization_url)

  await new Promise<void>((resolve, reject) => {
    const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL_NAME)
    const remaining = Math.max(1_000, new Date(authorization.expires_at).getTime() - Date.now())
    const timeout = window.setTimeout(() => finish(new Error('MCP authorization timed out.')), remaining)
    let poll: number | undefined
    let settled = false
    const closed = window.setInterval(() => {
      if (!oauthWindow.closed) return
      window.clearInterval(closed)
      onDetached?.()
      if (!alreadyAuthorized) poll = window.setInterval(checkStatus, STATUS_POLL_MS)
    }, 500)

    function finish(error?: Error) {
      if (settled) return
      settled = true
      window.clearTimeout(timeout)
      window.clearInterval(closed)
      window.clearInterval(poll)
      window.removeEventListener('message', onMessage)
      signal?.removeEventListener('abort', onAbort)
      channel?.close()
      if (!oauthWindow.closed) oauthWindow.close()
      if (error) reject(error)
      else resolve()
    }

    function onAbort() {
      finish(new Error('MCP authorization was cancelled.'))
    }

    async function checkStatus() {
      try {
        if (isOAuthAuthorized(await mcpServersApi.get(serverId))) finish()
      } catch {
        // Transient; the next tick, the callback message, or the timeout decides.
      }
    }

    function onResult(data: OAuthMessage | undefined) {
      if (data?.type !== MESSAGE_TYPE) return
      if (data.status === 'success' && data.serverId === serverId) finish()
      else finish(new Error(oauthErrorMessage(data.code)))
    }

    function onMessage(event: MessageEvent<OAuthMessage>) {
      if (event.origin !== window.location.origin || event.source !== oauthWindow) return
      onResult(event.data)
    }

    window.addEventListener('message', onMessage)
    if (channel) channel.onmessage = (event: MessageEvent<OAuthMessage>) => onResult(event.data)
    signal?.addEventListener('abort', onAbort)
  })
}

export function postMcpOAuthResult(params: URLSearchParams): void {
  const status = params.get('status') === 'success' ? 'success' : 'error'
  const message = {
    type: MESSAGE_TYPE,
    status,
    serverId: params.get('server_id') ?? undefined,
    code: params.get('code') ?? undefined,
  } satisfies OAuthMessage
  window.opener?.postMessage(message, window.location.origin)
  if (typeof BroadcastChannel !== 'undefined') {
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage(message)
    channel.close()
  }
}

function oauthErrorMessage(code?: string): string {
  if (code === 'authorization_rejected') return 'Authorization was rejected by the provider.'
  if (code === 'invalid_state') return 'The authorization request expired or was already used.'
  if (code === 'token_exchange_failed') return 'The provider rejected the authorization code.'
  return 'MCP authorization failed.'
}
