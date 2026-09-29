import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plug, ShieldCheck, Terminal } from 'lucide-react'
import { ApiError, mcpServersApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import { authorizeMcpServer } from '@/lib/mcpOAuth'
import { credentialsAreValid, credentialsRecord, emptyCredentialEntry, type CredentialEntry } from '@/lib/mcpCredentials'
import { McpCredentialFields } from './McpCredentialFields'
import type { McpServer } from '@/types/mcpServers'

// The two transports an MCP client can speak here. Core's MCPTransport also
// has "sse", deliberately left out: it's the deprecated predecessor of
// streamable HTTP, and offering a third option whose only honest description
// is "the old one" makes this form harder to answer, not more capable. A
// server that still only speaks SSE can be registered through the API.
const TRANSPORTS = [
  {
    value: 'stdio',
    label: 'stdio',
    icon: Terminal,
    // Core spawns the process itself and pipes JSON-RPC over its stdin/stdout,
    // so this is a LOCAL server -- on the machine running ASAREE, not this
    // browser's.
    hint: 'ASAREE launches the server as a local subprocess on its own machine.',
  },
  {
    value: 'http',
    label: 'Streamable HTTP',
    icon: Plug,
    hint: 'ASAREE dials a remote server over HTTP. Private/loopback addresses are refused unless the deployment allows them.',
  },
]

// The stdio executables core's allowlist permits (security/
// mcp_command_allowlist.py's ALLOWED_MCP_EXECUTABLES). Shown up front rather
// than discovered by submitting a command and reading a 422 back.
const ALLOWED_EXECUTABLES = 'python, python3, python3.11, python3.12, uv, node, npx, npm'

type AuthMode = 'none' | 'bearer' | 'headers' | 'oauth' | 'env'

const HTTP_AUTH_MODES: { value: AuthMode; label: string; hint: string }[] = [
  { value: 'none', label: 'None', hint: 'Connect without credentials.' },
  { value: 'bearer', label: 'Bearer token', hint: 'Send an Authorization: Bearer header.' },
  { value: 'headers', label: 'Headers', hint: 'Send one or more API-key or custom authentication headers.' },
  { value: 'oauth', label: 'OAuth', hint: 'Authorize through the server’s standards-based browser flow.' },
]

// Registers a brand-new MCP server connection the user types in, then hands it
// back so the caller can place an MCP Client Tool node bound to it.
//
// POST /mcp-servers doesn't just persist a row: core validates (stdio
// allowlist, SSRF guard), connects, and lists the server's tools before
// returning. So by the time this resolves the node can be created with a real
// tool list already on it -- and a server that couldn't be reached comes back
// with status 'error', reported here instead of surfacing later as an agent
// that mysteriously had no tools.
export function ConnectMcpServerDialog({
  open,
  onOpenChange,
  onConnected,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConnected?: (server: McpServer) => void
}) {
  const [name, setName] = useState('')
  const [transport, setTransport] = useState('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [authMode, setAuthMode] = useState<AuthMode>('none')
  const [bearerToken, setBearerToken] = useState('')
  const [credentials, setCredentials] = useState<CredentialEntry[]>([emptyCredentialEntry()])
  const [oauthScope, setOauthScope] = useState('')
  const [oauthServer, setOauthServer] = useState<McpServer | null>(null)
  const [oauthPending, setOauthPending] = useState(false)
  const [oauthError, setOauthError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const isStdio = transport === 'stdio'
  const endpoint = isStdio ? command.trim() : url.trim()
  const authIsValid = authMode === 'none' || authMode === 'oauth'
    || (authMode === 'bearer' && bearerToken.length > 0)
    || ((authMode === 'headers' || authMode === 'env') && credentialsAreValid(credentials))
  const canSubmit = name.trim().length > 0 && endpoint.length > 0 && authIsValid

  const connectMutation = useMutation({
    mutationFn: () =>
      mcpServersApi.create({
        name: name.trim(),
        transport,
        command: isStdio ? command.trim() : null,
        url: isStdio ? null : url.trim(),
        headers: !isStdio && authMode === 'bearer'
          ? { Authorization: `Bearer ${bearerToken}` }
          : !isStdio && authMode === 'headers' ? credentialsRecord(credentials) : null,
        server_env: isStdio && authMode === 'env' ? credentialsRecord(credentials) : null,
      }),
    onSuccess: (server) => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
      if (authMode === 'oauth') {
        setOauthServer(server)
        return
      }
      onConnected?.(server)
      reset()
      onOpenChange(false)
    },
  })

  function reset() {
    setName('')
    setTransport('stdio')
    setCommand('')
    setUrl('')
    setAuthMode('none')
    setBearerToken('')
    setCredentials([emptyCredentialEntry()])
    setOauthScope('')
    setOauthServer(null)
    setOauthPending(false)
    setOauthError(null)
    connectMutation.reset()
  }

  async function authorizeOAuth() {
    if (!oauthServer || oauthPending) return
    setOauthPending(true)
    setOauthError(null)
    try {
      await authorizeMcpServer(oauthServer.id, oauthScope.trim() || undefined)
      const connected = await mcpServersApi.get(oauthServer.id)
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
      onConnected?.(connected)
      reset()
      onOpenChange(false)
    } catch (error) {
      setOauthError(error instanceof Error ? error.message : 'MCP authorization failed.')
    } finally {
      setOauthPending(false)
    }
  }

  const errorMessage = !connectMutation.isError
    ? null
    : connectMutation.error instanceof ApiError && typeof connectMutation.error.detail === 'string'
      ? connectMutation.error.detail
      : 'Could not connect to that server. Please try again.'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className={HUD_ACCENT_RING_CLASSNAME}>
        <DialogHeader>
          <DialogTitle>Connect an MCP server</DialogTitle>
          <DialogDescription>
            ASAREE connects and lists the server&rsquo;s tools now, so you&rsquo;ll know straight away whether it works.
            The connection is saved to your account and reusable across protocols.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            if (canSubmit && !connectMutation.isPending) connectMutation.mutate()
          }}
        >
          {!oauthServer && <>
          <div className="space-y-1.5">
            <Label htmlFor="mcp-name">Name</Label>
            <Input
              id="mcp-name"
              autoFocus
              placeholder="my-search-server"
              className="font-mono"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            {/* Unique across the whole deployment, not just this account --
                worth saying, since the 409 that enforces it is otherwise a
                surprise. */}
            <p className="text-xs text-muted-foreground">
              How this connection is identified everywhere else. Must not already be taken.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label>Transport</Label>
            {/* Two big radio-style cards rather than a Select: the choice
                changes which field below it you fill in, so it reads better as
                a visible mode switch than as a collapsed dropdown. */}
            <div className="grid grid-cols-2 gap-2">
              {TRANSPORTS.map((option) => {
                const active = transport === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => {
                      setTransport(option.value)
                      setAuthMode('none')
                      setCredentials([emptyCredentialEntry()])
                    }}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] transition-colors ${
                      active ? 'border-primary bg-primary/10 ring-1 ring-primary/40' : 'bg-background hover:bg-muted'
                    }`}
                  >
                    <option.icon className={`size-4 shrink-0 ${active ? 'text-primary' : 'text-muted-foreground'}`} />
                    <span className="font-medium">{option.label}</span>
                  </button>
                )
              })}
            </div>
            <p className="text-xs text-muted-foreground">{TRANSPORTS.find((t) => t.value === transport)?.hint}</p>
          </div>
          </>}

          {!oauthServer && (isStdio ? (
            <div className="space-y-1.5">
              <Label htmlFor="mcp-command">Command</Label>
              <Input
                id="mcp-command"
                placeholder="npx -y @modelcontextprotocol/server-filesystem /data"
                className="font-mono"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Run directly, never through a shell -- no pipes, redirects, or <span className="font-mono">$VAR</span>.
                Must start with one of: <span className="font-mono">{ALLOWED_EXECUTABLES}</span>.
              </p>
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="mcp-url">URL</Label>
              <Input
                id="mcp-url"
                placeholder="https://example.com/mcp"
                className="font-mono"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">The server&rsquo;s streamable HTTP endpoint.</p>
            </div>
          ))}

          {!oauthServer && (
            <div className="space-y-2">
              <Label>Authentication</Label>
              <div className="grid grid-cols-2 gap-2">
                {(isStdio
                  ? [
                      { value: 'none' as const, label: 'None', hint: 'Launch without additional credentials.' },
                      { value: 'env' as const, label: 'Environment', hint: 'Pass encrypted environment credentials to the subprocess.' },
                    ]
                  : HTTP_AUTH_MODES
                ).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setAuthMode(option.value)}
                    className={`cursor-pointer rounded-lg border px-3 py-2 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] transition-colors ${
                      authMode === option.value ? 'border-primary bg-primary/10 ring-1 ring-primary/40' : 'bg-background hover:bg-muted'
                    }`}
                  >
                    <span className="font-medium">{option.label}</span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                {(isStdio
                  ? authMode === 'env' ? 'Secrets are encrypted at rest and passed only to this subprocess.' : 'Launch without additional credentials.'
                  : HTTP_AUTH_MODES.find((mode) => mode.value === authMode)?.hint)}
              </p>
              {authMode === 'bearer' && (
                <Input
                  aria-label="Bearer token"
                  type="password"
                  autoComplete="off"
                  className="font-mono"
                  placeholder="Bearer token"
                  value={bearerToken}
                  onChange={(event) => setBearerToken(event.target.value)}
                />
              )}
              {(authMode === 'headers' || authMode === 'env') && (
                <McpCredentialFields
                  entries={credentials}
                  onChange={setCredentials}
                  namePlaceholder={authMode === 'env' ? 'API_TOKEN' : 'X-API-Key'}
                />
              )}
              {authMode === 'oauth' && (
                <div className="space-y-1.5">
                  <Input
                    aria-label="OAuth scopes"
                    className="font-mono"
                    placeholder="Optional scopes"
                    value={oauthScope}
                    onChange={(event) => setOauthScope(event.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">Leave blank to use the scopes advertised by the server.</p>
                </div>
              )}
            </div>
          )}

          {oauthServer && (
            <div className="space-y-3 rounded-lg border p-4">
              <div className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 size-4 text-primary" />
                <div>
                  <p className="text-sm font-medium">Authorize {oauthServer.name}</p>
                  <p className="text-xs text-muted-foreground">The server is saved. Finish authorization in the provider window.</p>
                </div>
              </div>
              <Button type="button" className="w-full" disabled={oauthPending} onClick={authorizeOAuth}>
                <KeyRound className="size-4" />
                {oauthPending ? 'Waiting for authorization…' : 'Open authorization'}
              </Button>
            </div>
          )}

          {(errorMessage || oauthError) && <p className="text-sm text-destructive">{oauthError ?? errorMessage}</p>}

          {!oauthServer && <DialogFooter>
            <Button type="submit" disabled={!canSubmit || connectMutation.isPending}>
              {connectMutation.isPending ? 'Connecting…' : authMode === 'oauth' ? 'Continue' : 'Connect'}
            </Button>
          </DialogFooter>}
        </form>
      </DialogContent>
    </Dialog>
  )
}
