import { useState } from 'react'
import { Cog } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { NodeRunOutputPanel } from './NodeRunOutputPanel'
import type { NodeRunState, ProtocolNode, ToolStepNodeConfig, ToolStepNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('tool_step')

const SANITIZER_LABELS: Record<string, string> = {
  none: 'None -- send the payload unchanged',
  xgboost_hyperparameters: 'XGBoost hyperparameters',
}

// Same NodeInspectorDialog shell as CriticGateNodeInspector. `toolOptions` is
// the wired MCP Tool node's own enabled tools: a Tool Step may only call one
// its MCP node allow-lists (services/tool_steps.py's validate_tool_step), so
// the picker offers exactly those rather than a free-text name.
export function ToolStepNodeInspector({
  node,
  toolOptions,
  nodeRun,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: ToolStepNodeData }) | null
  toolOptions: string[]
  nodeRun?: NodeRunState
  onChange: (nodeId: string, data: ToolStepNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const [argumentsText, setArgumentsText] = useState(() => JSON.stringify(node?.data.config.arguments ?? {}, null, 2))
  const [argumentsError, setArgumentsError] = useState<string | null>(null)
  if (!node) return null
  const data = node.data
  const config = data.config
  const options = config.tool_name && !toolOptions.includes(config.tool_name) ? [config.tool_name, ...toolOptions] : toolOptions

  function patchConfig(patch: Partial<ToolStepNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function commitArguments(text: string) {
    setArgumentsText(text)
    try {
      const parsed: unknown = JSON.parse(text || '{}')
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setArgumentsError('Arguments must be a JSON object.')
        return
      }
      setArgumentsError(null)
      patchConfig({ arguments: parsed as Record<string, unknown> })
    } catch {
      setArgumentsError('Not valid JSON yet -- the last valid arguments are kept.')
    }
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
            Calls one MCP tool directly with the upstream node's JSON payload -- no model decides whether or how
            to call it. A wired Script's code is sent as <span className="font-mono">{config.code_argument || 'code'}</span>,
            the payload as <span className="font-mono">{config.payload_argument || 'payload_json'}</span>.
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

          <div className="space-y-1.5">
            <Label htmlFor="tool-step-sanitizer">Payload sanitizer</Label>
            <Select
              value={config.sanitizer || 'none'}
              onValueChange={(value) =>
                patchConfig({ sanitizer: value === 'xgboost_hyperparameters' ? 'xgboost_hyperparameters' : '' })
              }
            >
              <SelectTrigger id="tool-step-sanitizer" className="w-full">
                <SelectValue>{(value) => SANITIZER_LABELS[value ?? 'none']}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SANITIZER_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              XGBoost hyperparameters drops every suggestion outside the fixed search space (unknown or harness-fixed
              params, out-of-bound ranges, malformed entries) so the replicate still scores; each drop is recorded as a
              note on the step.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="tool-step-arguments">Fixed arguments</Label>
            <Textarea
              id="tool-step-arguments"
              rows={6}
              className="font-mono text-xs"
              value={argumentsText}
              onChange={(e) => commitArguments(e.target.value)}
            />
            {argumentsError ? (
              <p className="text-xs text-destructive">{argumentsError}</p>
            ) : (
              <p className="text-xs text-muted-foreground">Sent with every call, e.g. a random seed or the task type.</p>
            )}
          </div>

          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="tool-step-verify">Verify hashes</Label>
              <p className="text-xs text-muted-foreground">
                Fail the step unless the tool reports the exact script and payload hashes this step sent.
              </p>
            </div>
            <Switch
              id="tool-step-verify"
              checked={config.verify_hashes ?? true}
              onCheckedChange={(checked) => patchConfig({ verify_hashes: checked })}
            />
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
