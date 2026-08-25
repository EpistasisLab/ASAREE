import { ArrowRight } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { FactorBindableField, MakeNodeFactorButton } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import type { SingleAgentBaselinePatternConfig, SingleAgentBaselinePatternNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('pattern_single_agent_baseline')

// Fields mirror Motoro's own SingleAgentBaselinePattern.configuration_schema
// (engine/patterns/builtin/single_agent_baseline.py) exactly, and ARE wired
// to a real run -- see ReasonActPatternNodeInspector's own comment for how
// _resolve_pattern_config/PatternConfig thread this node's config into
// create_agent/update_agent. Each field is also factor-bindable.
// No Delete button in the header -- an agent's execution pattern must
// never go to zero (see ProtocolCanvas.tsx's nonDeletablePatternNodeIds),
// so this node is only ever removed by swapping it for a different one
// (the node's own canvas hover toolbar), never a bare delete.
export function SingleAgentBaselinePatternNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onClose,
}: {
  node: (ProtocolNode & { data: SingleAgentBaselinePatternNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's agentTracedLabel)
  // -- distinct from data.label, which is this node's own plain label shown
  // in the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: SingleAgentBaselinePatternNodeData) => void
  onClose: () => void
}) {
  const { requestMakeFactor } = useProtocolCanvasActions()

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<SingleAgentBaselinePatternConfig>) {
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
          <ArrowRight className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || 'Single-Agent Baseline'}</h2>
          <MakeNodeFactorButton onClick={() => requestMakeFactor(node.id)} />
        </>
      }
      onClose={onClose}
    >
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
            <Label htmlFor="baseline-max-iterations" className="flex items-center gap-1.5">
              Max iterations
              {trigger}
            </Label>
            <Input
              id="baseline-max-iterations"
              type="number"
              min="1"
              value={config.max_iterations}
              onChange={(e) => patchConfig({ max_iterations: Number(e.target.value) })}
            />
          </div>
        )}
      </FactorBindableField>

      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.stop_on_first_success"
        defaultLabel="Stop on first success"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.stop_on_first_success']}
        onBind={(name) => bindFactor('config.stop_on_first_success', name)}
        onUnbind={() => unbindFactor('config.stop_on_first_success')}
      >
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="baseline-stop-on-first-success" className="flex items-center gap-1.5">
                Stop on first success
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">Off: keeps looping for the full iteration budget even after a successful pass.</p>
            </div>
            <Switch
              id="baseline-stop-on-first-success"
              checked={config.stop_on_first_success}
              onCheckedChange={(checked) => patchConfig({ stop_on_first_success: checked })}
            />
          </div>
        )}
      </FactorBindableField>
    </NodeInspectorDialog>
  )
}
