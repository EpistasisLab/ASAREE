import { useQuery } from '@tanstack/react-query'
import { Wrench } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
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
// this node is a per-SERVER connection with an allow-list (matches n8n's
// own MCP Client Tool node and agentic-core's real allow-list primitive,
// see McpToolNodeConfig's own comment), not a node per individual tool. No
// Settings tab yet: there's nothing execution-constraint-shaped for a tool
// node the way budget/duration are for an agent.
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
        <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
          <div>
            <Label htmlFor="tool-enabled">Enabled</Label>
            <p className="text-xs text-muted-foreground">Off: this server's tools aren't offered to the agent at all.</p>
          </div>
          <Switch id="tool-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
        </div>
      </FactorBindableField>

      {serversQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : serversQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your registered MCP servers.</p>
        ) : !serversQuery.data || serversQuery.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No MCP servers registered yet. Register one via the API, then come back to pick a tool here.
          </p>
        ) : (
          <>
            <FactorBindableField
              experimentId={experimentId}
              fieldPath="config"
              defaultLabel="Server & tools"
              nodeLabel={factorNodeLabel}
              levelType="tool_config"
              currentValue={config}
              boundFactorName={bindings.config}
              onBind={(name) => bindFactor('config', name)}
              onUnbind={() => unbindFactor('config')}
            >
              <div className="space-y-1.5">
                <Label>Server</Label>
                <Select
                  value={config.server_id ?? '__none__'}
                  onValueChange={(value) => {
                    if (!value || value === '__none__') return
                    const server = serversQuery.data.find((s) => s.id === value)
                    // All tools allowed by default -- an allow-list that starts
                    // empty just means every tool silently does nothing until
                    // the user discovers they need to flip each one on; "All"
                    // is also already one click away if they want to narrow it.
                    patchConfig({
                      server_id: value,
                      server_name: server?.name ?? null,
                      tool_names: server?.capabilities?.tools?.map((t) => t.name) ?? [],
                    })
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue>{() => selectedServer?.name ?? 'Select a server…'}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__" disabled>
                      Select a server…
                    </SelectItem>
                    {serversQuery.data.map((server) => (
                      <SelectItem key={server.id} value={server.id}>
                        {server.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
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
              {!config.server_id ? (
                <p className="text-sm text-muted-foreground">Pick a server first.</p>
              ) : tools.length === 0 ? (
                <p className="text-sm text-muted-foreground">This server has no tools.</p>
              ) : (
                <div className="max-h-64 space-y-0.5 overflow-y-auto rounded-lg border p-1.5">
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
          </>
        )}
    </NodeInspectorDialog>
  )
}
