import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { runsApi } from '@/api/client'
import { nodeRunBadge } from '@/lib/protocolRun'
import { hashToChartHue } from '@/lib/utils'
import type { NodeRunState } from '@/types/protocols'
import type { RunStep } from '@/types/runs'

function toolCallSummary(toolCall: Record<string, unknown> | null): string | null {
  if (!toolCall) return null
  const name = toolCall.tool_name ?? toolCall.name
  return typeof name === 'string' ? `Tool: ${name}` : 'Tool call'
}

function StepRow({ step }: { step: RunStep }) {
  const [expanded, setExpanded] = useState(false)
  const accent = hashToChartHue(step.phase)
  const preview = toolCallSummary(step.tool_call) ?? (typeof step.output === 'string' ? step.output : null)

  return (
    <div className="rounded-lg border bg-background/50">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left"
      >
        {expanded ? (
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span
          className="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium uppercase tracking-wide"
          style={{ backgroundColor: `color-mix(in oklch, ${accent}, transparent 85%)`, color: accent }}
        >
          {step.phase}
        </span>
        {preview && <span className="truncate text-xs text-muted-foreground">{preview}</span>}
      </button>
      {expanded && (
        <div className="space-y-2 border-t px-3 py-2 font-mono text-xs">
          {([
            ['Input', step.input],
            ['Tool call', step.tool_call],
            ['LLM call', step.llm_call],
            ['Output', step.output],
          ] as const).map(
            ([heading, value]) =>
              value != null && (
                <div key={heading}>
                  <p className="mb-1 text-muted-foreground">{heading}</p>
                  <pre className="overflow-x-auto rounded bg-muted p-2 whitespace-pre-wrap">{JSON.stringify(value, null, 2)}</pre>
                </div>
              ),
          )}
        </div>
      )}
    </div>
  )
}

// The one piece of ARES's Run Detail page worth reusing here: real
// observability into what an agent actually did (its Sense/Reason/Plan/Act
// loop), not a wholesale port of that page. Zero new backend work --
// agentic-core already persists this per-run (RunStep), and ASAREE's own
// GET /runs/{id}/steps already exposes it; this is purely a new frontend
// view over data that already exists. Steps are fetched lazily (only once
// expanded), since most inspector opens just want the final output/error,
// already available from the same polled node_runs blob the canvas badge
// uses -- no separate request needed for that part.
export function NodeRunOutputPanel({ nodeRun }: { nodeRun: NodeRunState | undefined }) {
  const [stepsOpen, setStepsOpen] = useState(false)
  const badge = nodeRunBadge(nodeRun?.status)
  const stepsQuery = useQuery({
    queryKey: ['runs', nodeRun?.run_id, 'steps'],
    queryFn: () => runsApi.getSteps(nodeRun!.run_id!),
    enabled: stepsOpen && !!nodeRun?.run_id,
  })

  if (!nodeRun) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        This node hasn't run yet -- click Run on the canvas to see its output here.
      </p>
    )
  }

  const steps = stepsQuery.data ?? []
  const iterations = new Map<number, RunStep[]>()
  for (const step of steps) {
    const bucket = iterations.get(step.iteration) ?? []
    bucket.push(step)
    iterations.set(step.iteration, bucket)
  }

  return (
    <div className="space-y-4">
      {badge && (
        <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${badge.className}`}>{badge.label}</span>
      )}

      {nodeRun.error ? (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">Error</p>
          <p className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm whitespace-pre-wrap text-destructive">
            {nodeRun.error}
          </p>
        </div>
      ) : nodeRun.output_text ? (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">Output</p>
          <p className="max-h-64 overflow-y-auto rounded-lg border bg-muted/30 p-3 text-sm whitespace-pre-wrap">{nodeRun.output_text}</p>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No output yet.</p>
      )}

      {nodeRun.run_id && (
        <div className="space-y-1.5">
          <button
            type="button"
            onClick={() => setStepsOpen((o) => !o)}
            className="flex cursor-pointer items-center gap-1.5 text-sm font-medium text-muted-foreground hover:text-foreground"
          >
            {stepsOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
            Steps (Sense / Reason / Plan / Act)
          </button>
          {stepsOpen &&
            (stepsQuery.isLoading ? (
              <p className="text-xs text-muted-foreground">Loading steps…</p>
            ) : steps.length === 0 ? (
              <p className="text-xs text-muted-foreground">No step trace recorded for this run.</p>
            ) : (
              <div className="space-y-3">
                {[...iterations.entries()]
                  .sort(([a], [b]) => a - b)
                  .map(([iteration, iterationSteps]) => (
                    <div key={iteration} className="space-y-1.5">
                      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Iteration {iteration}</p>
                      <div className="space-y-1">
                        {iterationSteps
                          .sort((a, b) => a.sequence - b.sequence)
                          .map((step) => (
                            <StepRow key={step.id} step={step} />
                          ))}
                      </div>
                    </div>
                  ))}
              </div>
            ))}
        </div>
      )}
    </div>
  )
}
