import { Repeat2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { hashToChartHue } from '@/lib/utils'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { ReasonActPatternConfig, ReasonActPatternNodeData, ProtocolNode } from '@/types/protocols'

const OBSERVATION_FORMATS = ['raw', 'summarized'] as const
const ACCENT = hashToChartHue('pattern_reason_act')

// Fields mirror agentic-core's own ReasonActPattern.configuration_schema
// (engine/patterns/builtin/reason_act.py) exactly -- but nothing here is
// wired to a real run yet (see ReasonActPatternNodeData's own comment in
// types/protocols.ts): connecting this to an agent declares intent for a
// future phase, editing these fields has no effect on execution today.
// No Delete button in the header -- an agent's execution pattern must
// never go to zero (see ProtocolCanvas.tsx's nonDeletablePatternNodeIds),
// so this node is only ever removed by swapping it for a different one
// (the node's own canvas hover toolbar), never a bare delete.
export function ReasonActPatternNodeInspector({
  node,
  onChange,
  onClose,
}: {
  node: (ProtocolNode & { data: ReasonActPatternNodeData }) | null
  onChange: (nodeId: string, data: ReasonActPatternNodeData) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config

  function patchConfig(patch: Partial<ReasonActPatternConfig>) {
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
          <Repeat2 className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || 'Reason + Act'}</h2>
        </>
      }
      onClose={onClose}
    >
      <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        Not yet wired up to agentic-core's real reason_act pattern -- connecting this to an agent declares intent for
        a future phase, but has no effect on execution yet.
      </div>

      <div className="grid grid-cols-2 gap-4">
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
      </div>

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

      {config.include_scratchpad && (
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
      )}
    </NodeInspectorDialog>
  )
}
