import { Repeat2, Variable } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { hashToChartHue } from '@/lib/utils'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import type { ReasonActPatternConfig, ReasonActPatternNodeData, ProtocolNode } from '@/types/protocols'

const OBSERVATION_FORMATS = ['raw', 'summarized'] as const
const ACCENT = hashToChartHue('pattern_reason_act')

// Fields mirror agentic-core's own ReasonActPattern.configuration_schema
// (engine/patterns/builtin/reason_act.py) exactly -- but nothing here is
// wired to a real run yet (see ReasonActPatternNodeData's own comment in
// types/protocols.ts): connecting this to an agent declares intent for a
// future phase, editing these fields has no effect on execution today. Each
// field is still factor-bindable, though -- protocol_execution.py's
// _resolve_pattern_config reads the wired pattern node's own (already
// factor-patched) data.config verbatim, so varying e.g. max_iterations
// across cells already works with zero backend changes, same as it will the
// moment this pattern IS wired up for real.
// No Delete button in the header -- an agent's execution pattern must
// never go to zero (see ProtocolCanvas.tsx's nonDeletablePatternNodeIds),
// so this node is only ever removed by swapping it for a different one
// (the node's own canvas hover toolbar), never a bare delete.
export function ReasonActPatternNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onClose,
}: {
  node: (ProtocolNode & { data: ReasonActPatternNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's agentTracedLabel)
  // -- distinct from data.label, which is this node's own plain label shown
  // in the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: ReasonActPatternNodeData) => void
  onClose: () => void
}) {
  const { requestMakeFactor } = useProtocolCanvasActions()

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<ReasonActPatternConfig>) {
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
      accent={ACCENT}
      title={
        <>
          <Repeat2 className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || 'Reason + Act'}</h2>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Make experimental factor"
            title="Make experimental factor"
            onClick={() => requestMakeFactor(node.id)}
          >
            <Variable className="size-4" />
          </Button>
        </>
      }
      onClose={onClose}
    >
      <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        Not yet wired up to agentic-core's real reason_act pattern -- connecting this to an agent declares intent for
        a future phase, but has no effect on execution yet.
      </div>

      <div className="grid grid-cols-2 gap-4">
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.max_iterations"
          defaultLabel="Max iterations"
          nodeLabel={factorNodeLabel}
          levelType="number"
          currentValue={config.max_iterations}
          boundFactorName={bindings['config.max_iterations']}
          onBind={(name) => bindFactor('config.max_iterations', name)}
          onUnbind={() => unbindFactor('config.max_iterations')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="reason-act-max-iterations">Max iterations</Label>
            <Input
              id="reason-act-max-iterations"
              type="number"
              min="1"
              value={config.max_iterations}
              onChange={(e) => patchConfig({ max_iterations: Number(e.target.value) })}
            />
          </div>
        </FactorBindableField>
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.observation_format"
          defaultLabel="Observation format"
          nodeLabel={factorNodeLabel}
          levelType="string"
          currentValue={config.observation_format}
          levelOptions={OBSERVATION_FORMATS.map((f) => ({ value: f, label: f }))}
          boundFactorName={bindings['config.observation_format']}
          onBind={(name) => bindFactor('config.observation_format', name)}
          onUnbind={() => unbindFactor('config.observation_format')}
        >
          <div className="space-y-1.5">
            <Label>Observation format</Label>
            <Select value={config.observation_format} onValueChange={(value) => patchConfig({ observation_format: value as 'raw' | 'summarized' })}>
              <SelectTrigger className="w-full">
                <SelectValue>{(value: string) => value}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {OBSERVATION_FORMATS.map((format) => (
                  <SelectItem key={format} value={format}>
                    {format}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </FactorBindableField>
      </div>

      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.include_scratchpad"
        defaultLabel="Include scratchpad"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.include_scratchpad']}
        onBind={(name) => bindFactor('config.include_scratchpad', name)}
        onUnbind={() => unbindFactor('config.include_scratchpad')}
      >
        <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
          <div>
            <Label htmlFor="reason-act-scratchpad">Include scratchpad</Label>
            <p className="text-xs text-muted-foreground">Carries a running record of prior reasoning/observations into each iteration.</p>
          </div>
          <Switch
            id="reason-act-scratchpad"
            checked={config.include_scratchpad}
            onCheckedChange={(checked) => patchConfig({ include_scratchpad: checked })}
          />
        </div>
      </FactorBindableField>

      {config.include_scratchpad && (
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.scratchpad_window"
          defaultLabel="Scratchpad window"
          nodeLabel={factorNodeLabel}
          levelType="number"
          currentValue={config.scratchpad_window}
          boundFactorName={bindings['config.scratchpad_window']}
          onBind={(name) => bindFactor('config.scratchpad_window', name)}
          onUnbind={() => unbindFactor('config.scratchpad_window')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="reason-act-scratchpad-window">Scratchpad window</Label>
            <Input
              id="reason-act-scratchpad-window"
              type="number"
              min="1"
              value={config.scratchpad_window}
              onChange={(e) => patchConfig({ scratchpad_window: Number(e.target.value) })}
            />
          </div>
        </FactorBindableField>
      )}
    </NodeInspectorDialog>
  )
}
