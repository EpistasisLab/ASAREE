import { useQuery } from '@tanstack/react-query'
import { Trash2, Wrench, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { mcpServersApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { McpToolNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('mcp_tool')

// Same floating-dialog shell as AgentNodeInspector, but the "parameters" a
// tool node needs are just "which server, which tool" -- resolved from the
// caller's own registered MCP servers (GET /mcp-servers), not hand-typed.
// No Settings tab yet: there's nothing execution-constraint-shaped for a
// tool node the way budget/duration are for an agent.
export function McpToolNodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: McpToolNodeData }) | null
  onChange: (nodeId: string, data: McpToolNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const serversQuery = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list() })

  if (!node) return null
  const data = node.data
  const config = data.config
  const selectedServer = serversQuery.data?.find((s) => s.id === config.server_id)
  const tools = selectedServer?.capabilities?.tools ?? []

  function patchConfig(patch: Partial<McpToolNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  return (
    <NodeInspectorDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      header={
        <>
          <div className="flex items-center gap-2">
            <Wrench className="size-5" style={{ color: ACCENT }} />
            <h2 className="text-lg font-semibold">{data.label || 'MCP Tool'}</h2>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="Delete node" onClick={() => onDelete(node.id)}>
              <Trash2 className="size-4" />
            </Button>
            <Button variant="outline" size="icon" aria-label="Close" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </div>
        </>
      }
    >
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
            <div className="space-y-1.5">
              <Label>Server</Label>
              <Select
                value={config.server_id ?? undefined}
                onValueChange={(value) => {
                  if (value === null) return
                  const server = serversQuery.data.find((s) => s.id === value)
                  patchConfig({ server_id: value, server_name: server?.name ?? null, tool_name: null })
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>{() => selectedServer?.name ?? 'Select a server…'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {serversQuery.data.map((server) => (
                    <SelectItem key={server.id} value={server.id}>
                      {server.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Tool</Label>
              <Select
                value={config.tool_name ?? undefined}
                onValueChange={(value) => {
                  if (value !== null) patchConfig({ tool_name: value })
                }}
              >
                <SelectTrigger className="w-full" disabled={!config.server_id}>
                  <SelectValue>{() => config.tool_name ?? (config.server_id ? 'Select a tool…' : 'Pick a server first')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {tools.length === 0 ? (
                    <p className="px-2 py-1.5 text-sm text-muted-foreground">This server has no tools.</p>
                  ) : (
                    tools.map((tool) => (
                      <SelectItem key={tool.name} value={tool.name}>
                        {tool.name}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
              {config.tool_name && tools.find((t) => t.name === config.tool_name)?.description && (
                <p className="text-xs text-muted-foreground">{tools.find((t) => t.name === config.tool_name)?.description}</p>
              )}
            </div>
          </>
        )}
    </NodeInspectorDialog>
  )
}
