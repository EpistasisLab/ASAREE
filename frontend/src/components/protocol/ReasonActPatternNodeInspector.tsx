import { Repeat2 } from 'lucide-react'
import { useState } from 'react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { FactorBindableField, MakeNodeFactorButton } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import type { ReasonActPatternConfig, ReasonActPatternNodeData, ProtocolNode } from '@/types/protocols'

const OBSERVATION_FORMATS = ['raw', 'summarized'] as const
const ACCENT = nodeAccent('pattern_reason_act')

// Fields mirror Motoro's own ReasonActPattern.configuration_schema
// (engine/patterns/builtin/reason_act.py) exactly, and ARE wired to a real
// run: protocol_execution.py's _resolve_pattern_config reads this wired
// pattern node's own (already factor-patched) data.config into a real
// Motoro PatternConfig, passed straight into create_agent/update_agent
// -- editing max_iterations/observation_format/etc. here changes execution.
// Each field is also factor-bindable, so varying e.g. max_iterations across
// cells works the same way any other field-level binding does.
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
  // Shown instead of closing outright when a required field (see
  // ReasonActPatternNode.tsx's matching warning-triangle check) is still
  // empty -- lets the user close anyway rather than trapping them in the
  // inspector, but makes sure they saw it first.
  const [pendingCloseWarning, setPendingCloseWarning] = useState(false)

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  const missingFields: string[] = []
  if (config.max_iterations == null) missingFields.push('Max iterations')
  if (config.include_scratchpad && config.scratchpad_window == null) missingFields.push('Scratchpad window')

  function requestClose() {
    if (missingFields.length > 0) {
      setPendingCloseWarning(true)
      return
    }
    onClose()
  }

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
        if (!open) requestClose()
      }}
      accent={ACCENT}
      title={
        <>
          <Repeat2 className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || 'Reason + Act'}</h2>
          <MakeNodeFactorButton onClick={() => requestMakeFactor(node.id)} />
        </>
      }
      onClose={requestClose}
    >
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
          {(trigger) => (
            <div className="space-y-1.5">
              <Label htmlFor="reason-act-max-iterations" className="flex items-center gap-1.5">
                Max iterations
                {trigger}
              </Label>
              <Input
                id="reason-act-max-iterations"
                type="number"
                min="1"
                value={config.max_iterations ?? ''}
                onChange={(e) => patchConfig({ max_iterations: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </div>
          )}
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
          {(trigger) => (
            <div className="space-y-1.5">
              <Label className="flex items-center gap-1.5">
                Observation format
                {trigger}
              </Label>
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
          )}
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
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="reason-act-scratchpad" className="flex items-center gap-1.5">
                Include scratchpad
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">Carries a running record of prior reasoning/observations into each iteration.</p>
            </div>
            <Switch
              id="reason-act-scratchpad"
              checked={config.include_scratchpad}
              onCheckedChange={(checked) => patchConfig({ include_scratchpad: checked })}
            />
          </div>
        )}
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
          {(trigger) => (
            <div className="space-y-1.5">
              <Label htmlFor="reason-act-scratchpad-window" className="flex items-center gap-1.5">
                Scratchpad window
                {trigger}
              </Label>
              <Input
                id="reason-act-scratchpad-window"
                type="number"
                min="1"
                value={config.scratchpad_window ?? ''}
                onChange={(e) => patchConfig({ scratchpad_window: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </div>
          )}
        </FactorBindableField>
      )}

      <Dialog open={pendingCloseWarning} onOpenChange={(open) => !open && setPendingCloseWarning(false)}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Required fields are empty</DialogTitle>
            <DialogDescription>
              {missingFields.join(' and ')} {missingFields.length === 1 ? 'is' : 'are'} required for this pattern to run. You can close and fill{' '}
              {missingFields.length === 1 ? 'it' : 'them'} in later, but the node will stay flagged until you do.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingCloseWarning(false)}>
              Go back
            </Button>
            <Button onClick={onClose}>Close anyway</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </NodeInspectorDialog>
  )
}
