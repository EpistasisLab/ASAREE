import { afterEach, describe, expect, it, vi } from 'vitest'
import type { McpServer } from '@/types/mcpServers'
import { authorizeMcpServer, postMcpOAuthResult } from './mcpOAuth'

const api = vi.hoisted(() => ({ get: vi.fn(), beginOAuth: vi.fn() }))
vi.mock('@/api/client', () => ({ mcpServersApi: api }))

function server(authorized: boolean): McpServer {
  return {
    id: 'server-1',
    authentication: {
      auth_mode: authorized ? 'oauth' : 'none',
      configured: authorized,
      authorization_required: false,
      static_headers_configured: false,
      stdio_env_configured: false,
      stdio_env_names: [],
    },
  } as unknown as McpServer
}

function fakePopup() {
  return { closed: false, close: vi.fn(), location: { assign: vi.fn() } }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  api.get.mockReset()
  api.beginOAuth.mockReset()
})

describe('MCP OAuth callback', () => {
  it('posts only safe completion metadata to the opener origin', () => {
    const postMessage = vi.fn()
    vi.stubGlobal('opener', { postMessage })

    postMcpOAuthResult(new URLSearchParams('status=success&server_id=server-1'))

    expect(postMessage).toHaveBeenCalledWith(
      { type: 'asaree:mcp-oauth', status: 'success', serverId: 'server-1', code: undefined },
      window.location.origin,
    )
  })

  it('also broadcasts the result for openers severed by the provider', () => {
    const posted: unknown[] = []
    class FakeChannel {
      postMessage(message: unknown) { posted.push(message) }
      close() {}
    }
    vi.stubGlobal('opener', null)
    vi.stubGlobal('BroadcastChannel', FakeChannel)

    postMcpOAuthResult(new URLSearchParams('status=error&code=invalid_state'))

    expect(posted).toEqual([{ type: 'asaree:mcp-oauth', status: 'error', serverId: undefined, code: 'invalid_state' }])
  })
})

describe('authorizeMcpServer', () => {
  it('keeps waiting after the popup detaches and resolves once the server is authorized', async () => {
    vi.useFakeTimers()
    const popup = fakePopup()
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window)
    api.get.mockResolvedValueOnce(server(false)).mockResolvedValueOnce(server(true))
    api.beginOAuth.mockResolvedValue({
      authorization_url: 'https://auth.example/authorize',
      expires_at: new Date(Date.now() + 600_000).toISOString(),
      transaction_id: 't',
    })
    const onDetached = vi.fn()

    const done = authorizeMcpServer('server-1', undefined, { onDetached })
    await vi.advanceTimersByTimeAsync(0)
    popup.closed = true
    await vi.advanceTimersByTimeAsync(2_500)

    await expect(done).resolves.toBeUndefined()
    expect(onDetached).toHaveBeenCalledOnce()
  })

  it('rejects when the caller cancels', async () => {
    vi.useFakeTimers()
    vi.spyOn(window, 'open').mockReturnValue(fakePopup() as unknown as Window)
    api.get.mockResolvedValue(server(false))
    api.beginOAuth.mockResolvedValue({
      authorization_url: 'https://auth.example/authorize',
      expires_at: new Date(Date.now() + 600_000).toISOString(),
      transaction_id: 't',
    })
    const controller = new AbortController()

    const done = authorizeMcpServer('server-1', undefined, { signal: controller.signal })
    await vi.advanceTimersByTimeAsync(0)
    controller.abort()

    await expect(done).rejects.toThrow('cancelled')
  })
})
