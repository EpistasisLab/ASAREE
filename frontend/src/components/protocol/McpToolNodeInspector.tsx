import { useState } from 'react'
import { nodeAccent } from '@/lib/nodeAccent'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plug, RefreshCw, Wrench, Power, Split, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { mcpServersApi } from '@/api/client'
import { EditableNodeTitle } from './EditableNodeTitle'
import { MCP_CLIENT_TOOL_NODE_TYPE } from './mcpServerCatalog'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { ManageMcpCredentialsDialog } from './ManageMcpCredentialsDialog'
import type { McpToolNodeData, ProtocolNode } from '@/types/protocols'

// Kept in step with McpToolNode/McpClientToolNode's own accents -- the same
// node shouldn't change colour between the canvas and its inspector.
const ACCENT = nodeAccent('mcp_tool')
const CLIENT_ACCENT = nodeAccent(MCP_CLIENT_TOOL_NODE_TYPE)

const TRANSPORT_LABELS: Record<string, string> = { stdio: 'stdio', http: 'Streamable HTTP', sse: 'SSE' }

// Same floating-dialog shell as AgentNodeInspector, but the "parameters" a
// tool node needs are just "which server, which of its tools to allow" --
// resolved from the caller's own registered MCP servers (GET /mcp-servers),
// not hand-typed. Tools render as a toggle list, not a single-select --
// this node is a per-SERVER connection with an allow-list (matching
// Motoro's real allow-list primitive,
// see McpToolNodeConfig's own comment), not a node per individual tool. No
// Settings tab yet: there's nothing execution-constraint-shaped for a tool
// node the way budget/duration are for an agent.
//
// Serves both MCP-tool node types. Neither one shows a Server field at all:
// every MCP node is now created by picking a server in the MCP Servers
// browser (see mcpServerBrowserPanel/mcpServerCatalog.ts), which pins
// server_id/server_name onto the node's data, so a node IS one server and
// this inspector is purely "which of its tools may the agent call". The old
// dropdown -- and the whole-config "Server & tools" factor binding that hung
// off it -- are gone deliberately: reassigning a node's server after the
// fact contradicts the one-node-per-server model. Use a second MCP Servers
// node instead. Factor creation lives in the node toolbar; this inspector
// preserves the pinned server and edits its base allow-list.
export function McpToolNodeInspector({
  node,
  connectorFactorName,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: McpToolNodeData }) | null
  connectorFactorName?: string
  onChange: (nodeId: string, data: McpToolNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list() })
  const queryClient = useQueryClient()
  const [credentialsOpen, setCredentialsOpen] = useState(false)
  const serverId = node?.data.config.server_id ?? null

  // Re-dials and re-discovers tools, then writes the fresh list onto the node
  // -- the node's own tool_names is what a run allow-lists against, so a
  // reconnect that only updated the server row would leave the node stale.
  const reconnectMutation = useMutation({
    mutationFn: () => mcpServersApi.reconnect(serverId!),
    onSuccess: (server) => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] })
      const discovered = server.capabilities?.tools?.map((t) => t.name) ?? []
      if (node) {
        onChange(node.id, {
          ...node.data,
          config: { ...node.data.config, tool_names: node.data.config.tool_names.filter((n) => discovered.includes(n)) },
        })
      }
    },
  })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedServer = serversQuery.data?.find((s) => s.id === config.server_id)
  const tools = selectedServer?.capabilities?.tools ?? []
  const instructions = selectedServer?.capabilities?.instructions?.trim()
  const selectedTools = config.tool_names ?? []
  const isClientTool = node.type === MCP_CLIENT_TOOL_NODE_TYPE
  const canManageCredentials = selectedServer?.credential_management_allowed ?? false
  const accent = isClientTool ? CLIENT_ACCENT : ACCENT
  const Icon = isClientTool ? Plug : Wrench

  function patchConfig(patch: Partial<McpToolNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function toggleTool(name: string, allowed: boolean) {
    patchConfig({ tool_names: allowed ? [...selectedTools, name] : selectedTools.filter((t) => t !== name) })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(node!.id, { ...data, factor_bindings: next })
  }

  return (
    <NodeInspectorDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      accent={accent}
      title={
        <>
          <Icon className="size-5" style={{ color: accent }} />
          <EditableNodeTitle
            label={data.label}
            placeholder={isClientTool ? 'MCP Client Tool' : 'MCP Tool'}
            onCommit={(label) => onChange(node.id, { ...data, label })}
          />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="rounded-lg border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Tool node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon.
          {' '}<span className="ml-4 inline-flex flex-col gap-1 align-middle">
            <span className="text-[10px]">Toolbar preview</span>
            <span role="img" aria-label="Node toolbar preview: activate or deactivate, delete, and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3">
              <Power className="size-3" />
              <Trash2 className="size-3" />
              <Split className="size-3 text-chart-2" />
            </span>
          </span>
        </p>
      </div>
      {connectorFactorName ? <div className="space-y-2 rounded-lg border px-3 py-2"><div className="flex items-center justify-between"><Label>Enabled</Label><Switch checked disabled /></div><p className="text-xs text-muted-foreground">Availability is controlled by connector factor {connectorFactorName}. Remove it to restore individual controls.</p>{Object.entries(bindings).map(([path, name]) => <div key={path} className="space-y-1"><p className="text-xs text-destructive">Individual factor {name} conflicts with this connector.</p><Button variant="outline" size="sm" onClick={() => unbindFactor(path)}>Remove individual binding</Button><p className="text-xs text-muted-foreground">Its declaration remains in Design until you remove or rebind it.</p></div>)}</div> : <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
        <div><Label htmlFor="tool-enabled">Enabled</Label><p className="text-xs text-muted-foreground">Off: the wired agent never sees this server’s tools at all.</p>{bindings['config.enabled'] && <p className="text-xs text-chart-2">Factor: {bindings['config.enabled']}</p>}</div>
        <Switch id="tool-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
      </div>}

      {/* Which process this node actually talks to. Most valuable on a client
          tool -- its endpoint is something a user typed, so it's part of the
          experiment's record rather than deployment infrastructure -- but a
          preset node's connection is worth being able to confirm too. */}
      {selectedServer && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label>Connection</Label>
            <div className="flex gap-1">
              {canManageCredentials && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Manage credentials"
                  title="Replace, authorize, or clear credentials"
                  onClick={() => setCredentialsOpen(true)}
                >
                  <KeyRound className="size-3.5" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Reconnect"
                title="Reconnect and re-discover this server's tools"
                disabled={reconnectMutation.isPending}
                onClick={() => reconnectMutation.mutate()}
              >
                <RefreshCw className={`size-3.5 ${reconnectMutation.isPending ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </div>
          <div className="space-y-1 rounded-lg border px-3 py-2">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="font-mono text-xs">
                {TRANSPORT_LABELS[selectedServer.transport] ?? selectedServer.transport}
              </Badge>
              {selectedServer.status !== 'connected' && (
                <Badge variant="outline" className="text-destructive">
                  {selectedServer.status}
                </Badge>
              )}
              {selectedServer.authentication.authorization_required && (
                <Badge variant="outline" className="text-destructive">authorization required</Badge>
              )}
            </div>
            {/* dir="rtl" so a long command/URL truncates at the FRONT -- the
                distinguishing part of either one is at the end. */}
            <p className="truncate font-mono text-xs text-muted-foreground" dir="rtl" title={selectedServer.command ?? selectedServer.url ?? ''}>
              {selectedServer.command ?? selectedServer.url ?? '(no endpoint recorded)'}
            </p>
            {selectedServer.error_message && <p className="text-xs text-destructive">{selectedServer.error_message}</p>}
          </div>
          {/* The server's own `instructions` -- what it tells an agent it is,
              as opposed to the per-tool descriptions in the list below. Not
              truncated and not mono: it's prose the server author wrote for a
              reader, and it's the one thing here that answers "what is this
              server FOR" rather than "where does it run". Absent for a server
              that sends none, and for a row registered before this was
              captured -- reconnecting above fills that in. */}
          {instructions && <p className="whitespace-pre-line text-xs text-muted-foreground">{instructions}</p>}
        </div>
      )}

      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <div><Label>Tools allowed</Label>{bindings['config.tool_names'] && <p className="text-xs text-chart-2">Factor: {bindings['config.tool_names']}</p>}</div>
          {tools.length > 0 && <div className="flex gap-3 text-xs text-muted-foreground">
            <button type="button" className="hover:text-foreground" onClick={() => patchConfig({ tool_names: tools.map((tool) => tool.name) })}>All</button>
            <button type="button" className="hover:text-foreground" onClick={() => patchConfig({ tool_names: [] })}>None</button>
          </div>}
        </div>
        {connectorFactorName && <p className="text-xs text-muted-foreground">This allow-list is preserved when the connector selects this node.</p>}
        {serversQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : serversQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your registered MCP servers.</p>
        ) : !selectedServer ? (
          // The node names a server that GET /mcp-servers no longer returns
          // (deregistered, or a protocol JSON imported from another install).
          // There's deliberately no dropdown to repoint it -- replacing the
          // node from the MCP Servers panel is the fix.
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{config.server_name ?? 'This node’s server'}</span> isn&rsquo;t registered. Delete this node and
            add it again from the MCP Servers panel.
          </p>
        ) : tools.length === 0 ? (
          <p className="text-sm text-muted-foreground">This server has no tools.</p>
        ) : (
          // Sized off the viewport, not a fixed max-h: the inspector frame is
          // already full-height (NODE_INSPECTOR_CONTENT_CLASSNAME), so the
          // list should use whatever's left after the header and the Enabled
          // row rather than stopping short at 16rem and scrolling inside a
          // mostly-empty dialog.
          <div className="max-h-[calc(100vh-17rem)] min-h-40 space-y-0.5 overflow-y-auto rounded-lg border p-1.5">
            {tools.map((tool) => (
              <div key={tool.name} className="flex items-start justify-between gap-3 rounded-md px-1.5 py-1 hover:bg-muted">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{tool.name}</p>
                  {tool.description && (
                    <p className="truncate text-xs text-muted-foreground" title={tool.description}>
                      {tool.description}
                    </p>
                  )}
                </div>
                <Switch
                  size="sm"
                  className="mt-0.5 shrink-0"
                  checked={selectedTools.includes(tool.name)}
                  onCheckedChange={(allowed) => toggleTool(tool.name, allowed)}
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {selectedServer && canManageCredentials && credentialsOpen && (
        <ManageMcpCredentialsDialog
          server={selectedServer}
          open
          onOpenChange={setCredentialsOpen}
          onUpdated={(updated) => {
            const discovered = updated.capabilities?.tools?.map((tool) => tool.name) ?? []
            patchConfig({ tool_names: selectedTools.filter((name) => discovered.includes(name)) })
          }}
        />
      )}

    </NodeInspectorDialog>
  )
}
