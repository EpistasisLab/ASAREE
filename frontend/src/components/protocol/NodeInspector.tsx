import { useState } from 'react'
import { Bot, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { hashToChartHue } from '@/lib/utils'
import type { AgentNodeConfig, AgentNodeData, ProtocolNode } from '@/types/protocols'

const EXECUTION_PATTERNS = ['reason_act', 'single_agent_baseline'] as const
const ACCENT = hashToChartHue('agent')

// n8n opens a node's setup as a large centered floating window over the
// dimmed canvas (its Node Detail View), not a sidebar or an edge-to-edge
// takeover. Built on this app's own Dialog primitives (base-ui) rather than
// a hand-rolled overlay -- Escape-to-close, backdrop-click-to-close, focus
// trapping, and body scroll lock all come for free from `modal` (default
// true), instead of reimplementing them.
export function NodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: ProtocolNode | null
  onChange: (nodeId: string, data: AgentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  // Only "Advanced (JSON)" fields need a local staging string (so an
  // in-progress, momentarily-invalid edit doesn't get parsed on every
  // keystroke); every other field commits straight through onChange.
  const [advancedText, setAdvancedText] = useState<string | null>(null)
  const [advancedError, setAdvancedError] = useState<string | null>(null)

  if (!node) return null
  const data = node.data
  const config = data.config

  function patchConfig(patch: Partial<AgentNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function patchModelConfig(patch: Partial<AgentNodeConfig['model_config_data']>) {
    patchConfig({ model_config_data: { ...config.model_config_data, ...patch } })
  }

  const advanced = advancedText ?? JSON.stringify({ tool_config: config.tool_config, output_contract: config.output_contract }, null, 2)

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent
        showCloseButton={false}
        className="max-h-[85vh] w-full max-w-3xl overflow-y-auto sm:max-w-3xl"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot className="size-5" style={{ color: ACCENT }} />
            <h2 className="text-lg font-semibold">{data.label || 'Agent'}</h2>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" aria-label="Delete node" onClick={() => onDelete(node.id)}>
              <Trash2 className="size-4" />
            </Button>
            <Button variant="outline" size="icon" aria-label="Close" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="node-label">Label</Label>
            <Input id="node-label" value={data.label} onChange={(e) => onChange(node.id, { ...data, label: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-name">Name</Label>
            <Input id="node-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="node-goal">Goal</Label>
          <Textarea id="node-goal" rows={2} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="node-description">Description</Label>
          <Textarea
            id="node-description"
            rows={2}
            value={config.description}
            onChange={(e) => patchConfig({ description: e.target.value })}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="node-system-prompt">System prompt</Label>
          <Textarea
            id="node-system-prompt"
            rows={6}
            className="font-mono text-xs"
            value={config.system_prompt}
            onChange={(e) => patchConfig({ system_prompt: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-4 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="node-provider">Provider</Label>
            <Input
              id="node-provider"
              value={config.model_config_data.provider}
              onChange={(e) => patchModelConfig({ provider: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-model">Model</Label>
            <Input id="node-model" value={config.model_config_data.model} onChange={(e) => patchModelConfig({ model: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-temperature">Temperature</Label>
            <Input
              id="node-temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={config.model_config_data.temperature ?? ''}
              onChange={(e) => patchModelConfig({ temperature: e.target.value === '' ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-max-tokens">Max tokens</Label>
            <Input
              id="node-max-tokens"
              type="number"
              min="1"
              value={config.model_config_data.max_tokens}
              onChange={(e) => patchModelConfig({ max_tokens: Number(e.target.value) })}
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className="space-y-1.5">
            <Label>Execution pattern</Label>
            <Select
              value={config.pattern_config.execution_pattern}
              onValueChange={(value) => {
                if (value === null) return
                patchConfig({ pattern_config: { ...config.pattern_config, execution_pattern: value as typeof EXECUTION_PATTERNS[number] } })
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue>{(value: string) => value}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {EXECUTION_PATTERNS.map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-budget">Budget (USD)</Label>
            <Input
              id="node-budget"
              type="number"
              min="0"
              value={config.budget_limit_usd ?? ''}
              onChange={(e) => patchConfig({ budget_limit_usd: e.target.value === '' ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="node-duration">Max duration (s)</Label>
            <Input
              id="node-duration"
              type="number"
              min="0"
              value={config.max_run_duration_seconds ?? ''}
              onChange={(e) => patchConfig({ max_run_duration_seconds: e.target.value === '' ? null : Number(e.target.value) })}
            />
          </div>
        </div>

        <details className="space-y-1.5">
          <summary className="cursor-pointer text-sm font-medium text-muted-foreground">Advanced (JSON)</summary>
          <p className="pt-1.5 pb-1 text-xs text-muted-foreground">tool_config / output_contract</p>
          <Textarea
            rows={8}
            className="font-mono text-xs"
            value={advanced}
            onChange={(e) => {
              setAdvancedText(e.target.value)
              try {
                const parsed = JSON.parse(e.target.value)
                setAdvancedError(null)
                patchConfig({
                  tool_config: parsed.tool_config ?? config.tool_config,
                  output_contract: parsed.output_contract ?? null,
                })
              } catch {
                setAdvancedError('Invalid JSON')
              }
            }}
          />
          {advancedError && <p className="pt-1 text-xs text-destructive">{advancedError}</p>}
        </details>
      </DialogContent>
    </Dialog>
  )
}
