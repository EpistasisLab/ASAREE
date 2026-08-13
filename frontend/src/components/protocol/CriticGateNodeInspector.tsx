import { ShieldCheck, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { hashToChartHue } from '@/lib/utils'
import type { CriticGateNodeConfig, CriticGateNodeData, ProtocolNode } from '@/types/protocols'

const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
const ACCENT = hashToChartHue('critic_gate')

// Same floating-dialog shell as AgentNodeInspector/McpToolNodeInspector, but
// deliberately smaller: the critic never gets tools, always runs
// single-pass, and its output_contract is fixed by the executor
// (CRITIC_OUTPUT_CONTRACT) -- none of that belongs in this UI at all,
// unlike an Agent node's much larger config surface.
export function CriticGateNodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: CriticGateNodeData }) | null
  onChange: (nodeId: string, data: CriticGateNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config

  function patchConfig(patch: Partial<CriticGateNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function patchModelConfig(patch: Partial<CriticGateNodeConfig['model_config_data']>) {
    patchConfig({ model_config_data: { ...config.model_config_data, ...patch } })
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent showCloseButton={false} className="max-h-[85vh] w-full max-w-lg overflow-y-auto sm:max-w-lg">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="size-5" style={{ color: ACCENT }} />
            <h2 className="text-lg font-semibold">{data.label || 'Critic Gate'}</h2>
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

        <div className="flex items-center justify-between rounded-lg border px-3 py-2">
          <div>
            <Label htmlFor="gate-enabled">Enabled</Label>
            <p className="text-xs text-muted-foreground">
              Off: the upstream agent's output passes straight through, no review, no revisions.
            </p>
          </div>
          <Switch id="gate-enabled" checked={config.enabled} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-label">Label</Label>
          <Input id="gate-label" value={data.label} onChange={(e) => onChange(node.id, { ...data, label: e.target.value })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-name">Name</Label>
          <Input id="gate-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-goal">Review instructions</Label>
          <Textarea id="gate-goal" rows={3} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
          <p className="text-xs text-muted-foreground">
            What this critic checks for -- becomes its goal. It always returns a structured
            approved/feedback/rejection_scope verdict; that part isn't editable.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-system-prompt">System prompt</Label>
          <Textarea
            id="gate-system-prompt"
            rows={4}
            className="font-mono text-xs"
            value={config.system_prompt}
            onChange={(e) => patchConfig({ system_prompt: e.target.value })}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="gate-provider">Provider</Label>
            <Input
              id="gate-provider"
              value={config.model_config_data.provider}
              onChange={(e) => patchModelConfig({ provider: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="gate-model">Model</Label>
            <Input id="gate-model" value={config.model_config_data.model} onChange={(e) => patchModelConfig({ model: e.target.value })} />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="gate-temperature">Temperature</Label>
            <Input
              id="gate-temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={config.model_config_data.temperature ?? ''}
              onChange={(e) => patchModelConfig({ temperature: e.target.value === '' ? null : Number(e.target.value) })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Effort</Label>
            <Select
              value={config.model_config_data.effort ?? '__none__'}
              onValueChange={(value) => {
                if (value === null) return
                patchModelConfig({ effort: value === '__none__' ? null : value })
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue>{(value: string) => (value === '__none__' ? '(none)' : value)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">(none)</SelectItem>
                {EFFORT_LEVELS.map((level) => (
                  <SelectItem key={level} value={level}>
                    {level}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="gate-max-revisions">Max revisions</Label>
            <Input
              id="gate-max-revisions"
              type="number"
              min="0"
              value={config.max_revisions}
              onChange={(e) => patchConfig({ max_revisions: Number(e.target.value) })}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
