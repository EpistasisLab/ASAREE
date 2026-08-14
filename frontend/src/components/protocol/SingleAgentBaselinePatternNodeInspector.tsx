import { ArrowRight, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { SingleAgentBaselinePatternConfig, SingleAgentBaselinePatternNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('pattern_single_agent_baseline')

// Fields mirror agentic-core's own SingleAgentBaselinePattern.configuration_schema
// (engine/patterns/builtin/single_agent_baseline.py) exactly -- but nothing
// here is wired to a real run yet (see SingleAgentBaselinePatternNodeData's
// own comment in types/protocols.ts): connecting this to an agent declares
// intent for a future phase, editing these fields has no effect on
// execution today.
export function SingleAgentBaselinePatternNodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: SingleAgentBaselinePatternNodeData }) | null
  onChange: (nodeId: string, data: SingleAgentBaselinePatternNodeData) => void
  onDelete: (nodeId: string) => void
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
            <EditableNodeTitle
              label={data.label}
              placeholder="Single-Agent Baseline"
              onCommit={(label) => onChange(node.id, { ...data, label })}
            />
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
