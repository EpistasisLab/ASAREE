import { ArrowRight, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { hashToChartHue } from '@/lib/utils'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { SingleAgentBaselinePatternConfig, SingleAgentBaselinePatternNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('pattern_single_agent_baseline')

// Fields mirror agentic-core's own SingleAgentBaselinePattern.configuration_schema
// (engine/patterns/builtin/single_agent_baseline.py) exactly -- but nothing
// here is wired to a real run yet (see SingleAgentBaselinePatternNodeData's
// own comment in types/protocols.ts): connecting this to an agent declares
// intent for a future phase, editing these fields has no effect on
// execution today.
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
      header={
        <>
          <div className="flex items-center gap-2">
            <ArrowRight className="size-5" style={{ color: ACCENT }} />
            <h2 className="text-lg font-semibold">{data.label || 'Single-Agent Baseline'}</h2>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="icon" aria-label="Close" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </div>
        </>
      }
    >
      <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        Not yet wired up to agentic-core's real single_agent_baseline pattern -- connecting this to an agent declares
        intent for a future phase, but has no effect on execution yet.
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="baseline-max-iterations">Max iterations</Label>
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
          <Label htmlFor="baseline-stop-on-first-success">Stop on first success</Label>
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
