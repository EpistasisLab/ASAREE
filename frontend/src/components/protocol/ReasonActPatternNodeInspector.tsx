import { Repeat2, Split, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { nodeAccent } from '@/lib/nodeAccent'
import { isUnderIterated } from '@/lib/reasonActIterations'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { NodeInspectorDialog } from './NodeInspectorDialog'
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
  suggestedIterations,
  truncatedAt,
  onChange,
  onClose,
}: {
  node: (ProtocolNode & { data: ReasonActPatternNodeData }) | null
  // What the driven agent's wiring implies (lib/reasonActIterations.ts), or
  // null when this pattern drives no agent yet.
  suggestedIterations: number | null
  // The cap the last run actually died at, when it did -- evidence rather than
  // estimate, so the hint below cites it instead of the wiring.
  truncatedAt: number | null
  onChange: (nodeId: string, data: ReasonActPatternNodeData) => void
  onClose: () => void
}) {
  // Shown instead of closing outright when a required field (see
  // ReasonActPatternNode.tsx's matching warning-triangle check) is still
  // empty -- lets the user close anyway rather than trapping them in the
  // inspector, but makes sure they saw it first.
  const [pendingCloseWarning, setPendingCloseWarning] = useState(false)

  if (!node) return null
  const data = node.data
  const config = data.config

  // Only ever offered as a raise. Going below what the wiring needs truncates
  // the run into a payload of nulls that still reports as completed, while
  // going above it costs nothing -- the loop stops when the agent answers --
  // so there is no symmetric "you set this too high" to warn about.
  const underIterated = isUnderIterated(config.max_iterations, suggestedIterations)

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
        </>
      }
      onClose={requestClose}
    >
      <div className="rounded-lg border px-3 py-2"><p className="text-xs text-muted-foreground"><span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Pattern node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon. <span className="ml-4 inline-flex flex-col gap-1 align-middle"><span className="text-[10px]">Toolbar preview</span><span role="img" aria-label="Node toolbar preview: swap pattern and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3"><RefreshCw className="size-3" /><Split className="size-3 text-chart-2" /></span></span></p></div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="reason-act-max-iterations" className="flex items-center gap-1.5">
            Max iterations
          </Label>
          <Input
            id="reason-act-max-iterations"
            type="number"
            min="1"
            value={config.max_iterations ?? ''}
            onChange={(e) => patchConfig({ max_iterations: e.target.value === '' ? null : Number(e.target.value) })}
          />
          {underIterated && (
            <p className="text-xs text-[color:var(--chart-4)]">
              {truncatedAt != null
                ? `The last run stopped at ${truncatedAt} with its answer unwritten, so at least ${suggestedIterations} — `
                : `This agent's wiring suggests at least ${suggestedIterations} — `}
              each tool call costs an iteration, and a run that hits the cap stops mid-work.{' '}
              <button
                type="button"
                className="underline underline-offset-2 hover:no-underline"
                onClick={() => patchConfig({ max_iterations: suggestedIterations })}
              >
                Use {suggestedIterations}
              </button>
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label className="flex items-center gap-1.5">
            Observation format
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
      </div>

      <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
        <div>
          <Label htmlFor="reason-act-scratchpad" className="flex items-center gap-1.5">
            Include scratchpad
          </Label>
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
          <Label htmlFor="reason-act-scratchpad-window" className="flex items-center gap-1.5">
            Scratchpad window
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
