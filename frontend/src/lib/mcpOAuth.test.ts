import { describe, expect, it, vi } from 'vitest'
import { postMcpOAuthResult } from './mcpOAuth'

describe('MCP OAuth callback', () => {
  it('posts only safe completion metadata to the opener origin', () => {
    const postMessage = vi.fn()
    vi.stubGlobal('opener', { postMessage })

    postMcpOAuthResult(new URLSearchParams('status=success&server_id=server-1'))

    expect(postMessage).toHaveBeenCalledWith(
      { type: 'asaree:mcp-oauth', status: 'success', serverId: 'server-1', code: undefined },
      window.location.origin,
    )
    vi.unstubAllGlobals()
  })
})
