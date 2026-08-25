import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Plug, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { mcpServersApi } from '@/api/client'
import { selectableMcpServers } from './bindableFields'
import { ConnectMcpServerDialog } from './ConnectMcpServerDialog'
import { descriptionForServer, presetForServer } from './mcpServerCatalog'
import type { McpServer } from '@/types/mcpServers'

// The second level of the "Add Tool" drill-down: AddNodePanel's "MCP
// Servers" entry swaps this panel in, and picking a server here creates a
// node already bound to it (see mcpServerCatalog's nodeDataForServer). That
// ordering is the whole point -- choosing the server is how you add the
// node, rather than adding a blank node and then hunting for the server in
// a dropdown inside its inspector.
//
// The list is GET /mcp-servers, minus whatever selectableMcpServers hides,
// so a server the deployment registers (or a researcher registers for
// themselves) shows up here without a frontend change.
//
// Pinned above that list, always, is the MCP Client Tool row: it isn't one of
// the registered servers, it's how you add one that isn't here yet (a stdio
// subprocess or a streamable-HTTP endpoint). Pinned rather than sorted into
// the list because it's the answer to "none of these is what I want" -- which
// is exactly when a user has stopped reading the list -- and it stays put when
// the search box filters everything else away.
export function McpServerBrowserPanel({
  onPick,
  onConnect,
  onBack,
  onClose,
}: {
  onPick: (server: McpServer) => void
  // Fires with a server the user just registered through the connect dialog,
  // so the caller can place an MCP Client Tool node for it. Separate from
  // onPick because the node type differs -- see MCP_CLIENT_TOOL_NODE_TYPE.
  onConnect: (server: McpServer) => void
  onBack: () => void
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [connectOpen, setConnectOpen] = useState(false)
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list() })
  const servers = selectableMcpServers(serversQuery.data ?? [])
  const term = query.trim().toLowerCase()
  const filtered = servers.filter(
    (s) => s.name.toLowerCase().includes(term) || presetForServer(s).label.toLowerCase().includes(term),
  )

  return (
    <div className="flex w-80 shrink-0 flex-col gap-3 border-l p-4">
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1.5">
          <Button variant="ghost" size="icon-sm" aria-label="Back" onClick={onBack}>
            <ArrowLeft className="size-4" />
          </Button>
          <p className="truncate text-sm font-semibold">MCP Servers</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>
      <Input autoFocus placeholder="Search servers…" value={query} onChange={(e) => setQuery(e.target.value)} />

      {/* Outside the loading/error/empty branches below on purpose: connecting
          a server is exactly what you want to do when the list failed to load
          or has nothing in it. */}
      <button
        type="button"
        onClick={() => setConnectOpen(true)}
        className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/30 transition-colors hover:bg-primary/10"
      >
        <Plug className="mt-0.5 size-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">MCP Client Tool</p>
          <p className="text-xs text-muted-foreground">
            Connect your own server -- a local stdio command, or a remote streamable HTTP endpoint
          </p>
        </div>
      </button>

      <ConnectMcpServerDialog
        open={connectOpen}
        onOpenChange={setConnectOpen}
        onConnected={(server) => {
          setConnectOpen(false)
          onConnect(server)
        }}
      />

      {serversQuery.isLoading ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : serversQuery.isError ? (
        <p className="py-4 text-center text-sm text-destructive">Could not load MCP servers.</p>
      ) : filtered.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">
          {servers.length === 0 ? 'No MCP servers available.' : 'No matching servers.'}
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {filtered.map((server) => {
            const preset = presetForServer(server)
            const toolCount = server.capabilities?.tools?.length ?? 0
            return (
              <button
                key={server.id}
                type="button"
                onClick={() => onPick(server)}
                className="flex cursor-pointer items-start gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
              >
                <preset.icon className="mt-0.5 size-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{preset.label}</p>
                  <p className="truncate text-xs text-muted-foreground" title={descriptionForServer(server)}>
                    {descriptionForServer(server)}
                  </p>
                  {/* The registered id and tool count, monospaced -- the
                      precise, technical identity behind the friendly label,
                      so it's unambiguous which process is being wired in. */}
                  <p className="truncate font-mono text-[11px] text-muted-foreground/70">
                    {server.name} · {toolCount} {toolCount === 1 ? 'tool' : 'tools'}
                  </p>
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
