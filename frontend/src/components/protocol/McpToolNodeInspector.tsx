import { useQuery } from '@tanstack/react-query'
import { Wrench } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { mcpServersApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { McpToolNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('mcp_tool')

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
// node instead.
export function McpToolNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: McpToolNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's
  // agentTracedLabel) -- distinct from data.label, which is this node's own
  // plain label shown in the header title. Only used to scope this node's
  // "+ Make experimental factor" names, e.g. "Research Agent:Search API:
  // Enabled" instead of an ambiguous plain "Search API:Enabled" that can't
  // tell two agents' identically-labeled tool nodes apart.
  factorNodeLabel: string
  onChange: (nodeId: string, data: McpToolNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list() })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedServer = serversQuery.data?.find((s) => s.id === config.server_id)
  const tools = selectedServer?.capabilities?.tools ?? []
  const selectedTools = config.tool_names ?? []

  function patchConfig(patch: Partial<McpToolNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function toggleTool(name: string, allowed: boolean) {
    patchConfig({ tool_names: allowed ? [...selectedTools, name] : selectedTools.filter((t) => t !== name) })
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(node!.id, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
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
      accent={ACCENT}
      title={
        <>
          <Wrench className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="MCP Tool" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.enabled"
        defaultLabel="Enabled"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.enabled']}
        onBind={(name) => bindFactor('config.enabled', name)}
        onUnbind={() => unbindFactor('config.enabled')}
      >
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="tool-enabled" className="flex items-center gap-1.5">
                Enabled
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">Off: this server's tools aren't offered to the agent at all.</p>
            </div>
            <Switch id="tool-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
          </div>
        )}
      </FactorBindableField>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>Tools allowed</Label>
          {tools.length > 0 && (
            <div className="flex gap-3 text-xs text-muted-foreground">
              <button type="button" className="hover:text-foreground" onClick={() => patchConfig({ tool_names: tools.map((t) => t.name) })}>
                All
              </button>
              <button type="button" className="hover:text-foreground" onClick={() => patchConfig({ tool_names: [] })}>
                None
              </button>
            </div>
          )}
        </div>
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
    </NodeInspectorDialog>
  )
}
