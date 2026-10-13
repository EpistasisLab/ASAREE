import { ArrowRight, Split, RefreshCw } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { NodeInspectorDialog } from './NodeInspectorDialog'
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
  onChange,
  onClose,
}: {
  node: (ProtocolNode & { data: SingleAgentBaselinePatternNodeData }) | null
  onChange: (nodeId: string, data: SingleAgentBaselinePatternNodeData) => void
  onClose: () => void
}) {

  if (!node) return null
  const data = node.data
  const config = data.config

  function patchConfig(patch: Partial<SingleAgentBaselinePatternConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
        </>
      }
      onClose={onClose}
    >
      <div className="rounded-lg border px-3 py-2"><p className="text-xs text-muted-foreground"><span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Pattern node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon. <span className="ml-4 inline-flex flex-col gap-1 align-middle"><span className="text-[10px]">Toolbar preview</span><span role="img" aria-label="Node toolbar preview: swap pattern and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3"><RefreshCw className="size-3" /><Split className="size-3 text-chart-2" /></span></span></p></div>
      <div className="space-y-1.5">
        <Label htmlFor="baseline-max-iterations" className="flex items-center gap-1.5">
          Max iterations
        </Label>
        <Input
          id="baseline-max-iterations"
          type="number"
          min="1"
          value={config.max_iterations}
          onChange={(e) => patchConfig({ max_iterations: Number(e.target.value) })}
        />
      </div>

      <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
        <div>
          <Label htmlFor="baseline-stop-on-first-success" className="flex items-center gap-1.5">
            Stop on first success
          </Label>
          <p className="text-xs text-muted-foreground">Off: keeps looping for the full iteration budget even after a successful pass.</p>
        </div>
        <Switch
          id="baseline-stop-on-first-success"
          checked={config.stop_on_first_success}
          onCheckedChange={(checked) => patchConfig({ stop_on_first_success: checked })}
        />
      </div>
    </NodeInspectorDialog>
  )
}
