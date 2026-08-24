import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plug, Terminal } from 'lucide-react'
import { ApiError, mcpServersApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
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
  const queryClient = useQueryClient()

  const isStdio = transport === 'stdio'
  const endpoint = isStdio ? command.trim() : url.trim()
  const canSubmit = name.trim().length > 0 && endpoint.length > 0

  const connectMutation = useMutation({
    mutationFn: () =>
      mcpServersApi.create({
        name: name.trim(),
        transport,
        command: isStdio ? command.trim() : null,
        url: isStdio ? null : url.trim(),
      }),
    onSuccess: (server) => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
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
    connectMutation.reset()
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
                    onClick={() => setTransport(option.value)}
                    className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
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

          {isStdio ? (
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
          )}

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button type="submit" disabled={!canSubmit || connectMutation.isPending}>
              {connectMutation.isPending ? 'Connecting…' : 'Connect'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
