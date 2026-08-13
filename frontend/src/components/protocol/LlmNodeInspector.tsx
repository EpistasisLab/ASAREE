import { Cpu, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { hashToChartHue } from '@/lib/utils'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { LlmNodeConfig, LlmNodeData, ProtocolNode } from '@/types/protocols'

const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
const ACCENT = hashToChartHue('llm')

// Same fixed-size NodeInspectorDialog shell as every other node inspector.
// Exactly the Provider/Model/Temperature/Effort/Max tokens fields
// AgentNodeInspector/CriticGateNodeInspector used to have, relocated here --
// this is now the ONLY place that config lives, resolved at execution time
// via the agent/critic_gate's required LLM connector
// (services.protocol_execution's _resolve_llm_config).
export function LlmNodeInspector({
  node,
  experimentId,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: LlmNodeData }) | null
  experimentId: string | null
  onChange: (nodeId: string, data: LlmNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<LlmNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
      header={
        <>
          <div className="flex items-center gap-2">
            <Cpu className="size-5" style={{ color: ACCENT }} />
            <h2 className="text-lg font-semibold">{data.label || 'LLM'}</h2>
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
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="llm-provider">Provider</Label>
          <Input id="llm-provider" value={config.provider} onChange={(e) => patchConfig({ provider: e.target.value })} />
        </div>
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.model"
          defaultLabel="Model"
          levelType="string"
          boundFactorName={bindings['config.model']}
          onBind={(name) => bindFactor('config.model', name)}
          onUnbind={() => unbindFactor('config.model')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="llm-model">Model</Label>
            <Input id="llm-model" value={config.model} onChange={(e) => patchConfig({ model: e.target.value })} />
          </div>
        </FactorBindableField>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.temperature"
          defaultLabel="Temperature"
          levelType="number"
          boundFactorName={bindings['config.temperature']}
          onBind={(name) => bindFactor('config.temperature', name)}
          onUnbind={() => unbindFactor('config.temperature')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="llm-temperature">Temperature</Label>
            <Input
              id="llm-temperature"
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={config.temperature ?? ''}
              onChange={(e) => patchConfig({ temperature: e.target.value === '' ? null : Number(e.target.value) })}
            />
          </div>
        </FactorBindableField>
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.effort"
          defaultLabel="Effort"
          levelType="string"
          boundFactorName={bindings['config.effort']}
          onBind={(name) => bindFactor('config.effort', name)}
          onUnbind={() => unbindFactor('config.effort')}
        >
          <div className="space-y-1.5">
            <Label>Effort</Label>
            <Select
              value={config.effort ?? '__none__'}
              onValueChange={(value) => {
                if (value === null) return
                patchConfig({ effort: value === '__none__' ? null : value })
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
        </FactorBindableField>
        <div className="space-y-1.5">
          <Label htmlFor="llm-max-tokens">Max tokens</Label>
          <Input
            id="llm-max-tokens"
            type="number"
            min="1"
            value={config.max_tokens}
            onChange={(e) => patchConfig({ max_tokens: Number(e.target.value) })}
          />
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
