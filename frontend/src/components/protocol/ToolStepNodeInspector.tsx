import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Cog, Plus, X } from 'lucide-react'
import { mcpServersApi } from '@/api/client'
import { nodeAccent } from '@/lib/nodeAccent'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { NodeRunOutputPanel } from './NodeRunOutputPanel'
import type {
  NodeRunState,
  ProtocolNode,
  ToolStepArgumentSource,
  ToolStepNodeConfig,
  ToolStepNodeData,
} from '@/types/protocols'

const ACCENT = nodeAccent('tool_step')

type SourceKey = 'unset' | 'value' | 'payload_json' | 'payload_object' | 'script_code' | 'workspace_id'

const SOURCE_LABELS: Record<SourceKey, string> = {
  unset: 'Not sent',
  value: 'Fixed value',
  payload_json: 'Upstream payload (JSON text)',
  payload_object: 'Upstream payload (object)',
  script_code: "Wired Script's code",
  workspace_id: 'Workspace ID',
}

interface SchemaProperty {
  type?: string
  description?: string
}

function sourceKey(argument: ToolStepArgumentSource | undefined): SourceKey {
  if (!argument) return 'unset'
  if (argument.source === 'upstream_payload') return argument.format === 'object' ? 'payload_object' : 'payload_json'
  return argument.source
}

function argumentFor(key: SourceKey, schemaType: string | undefined): ToolStepArgumentSource | undefined {
  switch (key) {
    case 'unset':
      return undefined
    case 'value':
      return { source: 'value', value: schemaType === 'string' ? '' : null }
    case 'payload_json':
      return { source: 'upstream_payload', format: 'json_string' }
    case 'payload_object':
      return { source: 'upstream_payload', format: 'object' }
    default:
      return { source: key }
  }
}

function valueText(value: unknown): string {
  if (value === null || value === undefined) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

// A string-typed parameter keeps exactly what was typed; anything else is read
// as JSON when it parses (so `7` is a number, `true` a boolean), else as text.
function parseValue(text: string, schemaType: string | undefined): unknown {
  if (schemaType === 'string') return text
  if (text.trim() === '') return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function schemaProperties(inputSchema: unknown): Record<string, SchemaProperty> | null {
  if (!inputSchema || typeof inputSchema !== 'object') return null
  const properties = (inputSchema as { properties?: unknown }).properties
  return properties && typeof properties === 'object' ? (properties as Record<string, SchemaProperty>) : null
}

// Same NodeInspectorDialog shell as CriticGateNodeInspector. `toolOptions` is
// the wired MCP Tool node's own enabled tools: a Tool Step may only call one
// its MCP node allow-lists (services/tool_steps.py's validate_tool_step), so
// the picker offers exactly those rather than a free-text name. The argument
// rows come from the selected tool's own input schema, as its server
// published it -- nothing here knows any particular tool's parameters.
export function ToolStepNodeInspector({
  node,
  toolOptions,
  serverId,
  hasScript,
  nodeRun,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: ToolStepNodeData }) | null
  toolOptions: string[]
  serverId: string | null
  hasScript: boolean
  nodeRun?: NodeRunState
  onChange: (nodeId: string, data: ToolStepNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const serversQuery = useQuery({
    queryKey: ['mcp-servers'],
    queryFn: mcpServersApi.list,
    staleTime: 60_000,
    enabled: !!serverId,
  })
  const [newArgument, setNewArgument] = useState('')
  if (!node) return null
  const data = node.data
  const config = data.config
  const mapped = config.arguments ?? {}
  const hashChecks = config.hash_checks ?? {}
  const options = config.tool_name && !toolOptions.includes(config.tool_name) ? [config.tool_name, ...toolOptions] : toolOptions
  const tool = serversQuery.data
    ?.find((server) => server.id === serverId)
    ?.capabilities?.tools?.find((candidate) => candidate.name === config.tool_name)
  const properties = schemaProperties(tool?.input_schema)
  // Schema parameters first, in the server's order, then anything mapped that
  // the schema doesn't list (or all of them when no schema is available).
  const argumentNames = [
    ...Object.keys(properties ?? {}),
    ...Object.keys(mapped).filter((name) => !properties || !(name in properties)),
  ]

  function patchConfig(patch: Partial<ToolStepNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function setArgument(name: string, argument: ToolStepArgumentSource | undefined) {
    const next = { ...mapped }
    if (argument) next[name] = argument
    else delete next[name]
    // A hash check on an argument that's no longer sent could never pass.
    const checks = Object.fromEntries(Object.entries(hashChecks).filter(([, arg]) => arg in next))
    patchConfig({ arguments: next, hash_checks: checks })
  }

  function setHashCheck(oldField: string, field: string, argument: string) {
    const entries = Object.entries(hashChecks).map(([f, a]) => (f === oldField ? [field, argument] : [f, a]))
    patchConfig({ hash_checks: Object.fromEntries(entries) })
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
          <Cog className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Tool Step" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="flex h-full gap-4">
        <div className="min-w-0 flex-1 space-y-4 overflow-y-auto">
          <p className="text-xs text-muted-foreground">
            Calls one MCP tool directly -- no model decides whether or how to call it. Choose where each argument&apos;s
            value comes from; an argument that isn&apos;t mapped isn&apos;t sent.
          </p>

          <div className="space-y-1.5">
            <Label htmlFor="tool-step-tool">Tool</Label>
            <Select value={config.tool_name || null} onValueChange={(value) => patchConfig({ tool_name: value ?? '' })}>
              <SelectTrigger id="tool-step-tool" className="w-full font-mono">
                <SelectValue>
                  {(value) => value || (options.length ? 'Select a tool…' : 'Wire an MCP Tool node to the Tool connector')}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {options.map((name) => (
                  <SelectItem key={name} value={name} className="font-mono">
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {config.tool_name && (
            <div className="space-y-2">
              <Label>Arguments</Label>
              {!properties && (
                <p className="text-xs text-muted-foreground">
                  {serversQuery.isLoading
                    ? 'Loading the tool’s parameters…'
                    : 'This tool’s parameters aren’t available -- add arguments by name.'}
                </p>
              )}
              {argumentNames.map((name) => {
                const property = properties?.[name]
                const argument = mapped[name]
                const key = sourceKey(argument)
                const text = argument?.source === 'value' ? valueText(argument.value) : ''
                return (
                  <div key={name} className="space-y-1.5 rounded-lg border px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-xs" title={property?.description ?? name}>
                        {name}
                        {property?.type && <span className="text-muted-foreground"> : {property.type}</span>}
                      </span>
                      <Select
                        value={key}
                        onValueChange={(value) => setArgument(name, argumentFor((value ?? 'unset') as SourceKey, property?.type))}
                      >
                        <SelectTrigger className="w-60" aria-label={`Source for ${name}`}>
                          <SelectValue>{(value) => SOURCE_LABELS[(value ?? 'unset') as SourceKey]}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {(Object.keys(SOURCE_LABELS) as SourceKey[]).map((option) => (
                            <SelectItem key={option} value={option} disabled={option === 'script_code' && !hasScript}>
                              {SOURCE_LABELS[option]}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {key === 'value' &&
                      (text.length > 60 || text.includes('\n') ? (
                        <Textarea
                          rows={4}
                          className="font-mono text-xs"
                          aria-label={`Value for ${name}`}
                          value={text}
                          onChange={(e) => setArgument(name, { source: 'value', value: parseValue(e.target.value, property?.type) })}
                        />
                      ) : (
                        <Input
                          className="font-mono text-xs"
                          aria-label={`Value for ${name}`}
                          value={text}
                          onChange={(e) => setArgument(name, { source: 'value', value: parseValue(e.target.value, property?.type) })}
                        />
                      ))}
                  </div>
                )
              })}
              {!properties && (
                <div className="flex gap-2">
                  <Input
                    className="font-mono text-xs"
                    placeholder="argument name"
                    value={newArgument}
                    onChange={(e) => setNewArgument(e.target.value)}
                  />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!newArgument.trim() || newArgument.trim() in mapped}
                    onClick={() => {
                      setArgument(newArgument.trim(), { source: 'value', value: null })
                      setNewArgument('')
                    }}
                  >
                    <Plus className="size-3.5" /> Add
                  </Button>
                </div>
              )}
            </div>
          )}

          <div className="space-y-2">
            <Label>Hash checks</Label>
            <p className="text-xs text-muted-foreground">
              Fail the step unless the tool reports, in the named result field, the SHA-256 of an argument exactly as
              sent -- e.g. proof it ran the wired script verbatim.
            </p>
            {Object.entries(hashChecks).map(([field, argument]) => (
              <div key={field} className="flex items-center gap-2">
                <Input
                  className="font-mono text-xs"
                  aria-label="Result field"
                  defaultValue={field}
                  onBlur={(e) => {
                    const next = e.target.value.trim()
                    if (next && next !== field && !(next in hashChecks)) setHashCheck(field, next, argument)
                  }}
                />
                <span className="shrink-0 text-xs text-muted-foreground">= sha256 of</span>
                <Select value={argument} onValueChange={(value) => value && setHashCheck(field, field, value)}>
                  <SelectTrigger className="w-44 font-mono" aria-label="Argument">
                    <SelectValue>{(value) => value}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {Object.keys(mapped).map((name) => (
                      <SelectItem key={name} value={name} className="font-mono">
                        {name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove hash check ${field}`}
                  onClick={() =>
                    patchConfig({ hash_checks: Object.fromEntries(Object.entries(hashChecks).filter(([f]) => f !== field)) })
                  }
                >
                  <X className="size-3.5" />
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              disabled={Object.keys(mapped).length === 0}
              onClick={() => {
                let field = 'sha256'
                for (let i = 2; field in hashChecks; i++) field = `sha256_${i}`
                patchConfig({ hash_checks: { ...hashChecks, [field]: Object.keys(mapped)[0] } })
              }}
            >
              <Plus className="size-3.5" /> Add hash check
            </Button>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="tool-step-timeout">Timeout (seconds)</Label>
            <Input
              id="tool-step-timeout"
              type="number"
              min="1"
              value={config.timeout_seconds ?? ''}
              placeholder="No timeout"
              onChange={(e) => patchConfig({ timeout_seconds: e.target.value ? Number(e.target.value) : null })}
            />
          </div>
        </div>

        <div className="w-96 shrink-0 space-y-3 overflow-y-auto border-l pl-4">
          <p className="text-sm font-semibold">Output</p>
          <NodeRunOutputPanel nodeRun={nodeRun} />
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
