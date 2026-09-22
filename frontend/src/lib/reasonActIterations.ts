import type { ProtocolGraph, ProtocolNode } from '@/types/protocols'
import { isMcpToolNodeType } from '@/lib/mcpToolMetrics'

// What one wired thing costs the loop, in iterations. Measured from a real
// run rather than guessed (protocol "ML Reasoning Experiment", 4 Script nodes
// + Dataset + Skill, max_iterations 15): the agent spends ONE tool call per
// iteration, and a script is never one call -- it runs the script, then
// spends further iterations writing throwaway Python to read the JSON the
// script left on disk. Those 4 scripts consumed 13 iterations and the run was
// still mid-work when the cap stopped it, so the report was never written and
// its Output Parser had only a tool dump to read (every field came back
// null). Everything else -- an MCP tool, a skill, a dataset, a knowledge
// bundle -- is closer to a single load/describe call.
const SCRIPT_ITERATIONS = 5
const CONNECTOR_ITERATIONS = 2
// Session setup (open_workspace/load_skill) plus the iteration in which the
// agent finally writes its answer -- the one that matters most, because an
// answer that never gets written is exactly the failure this is sized to
// avoid.
const BASE_ITERATIONS = 4
// The catalog schema's own bounds (Motoro's reason_act configuration_schema:
// minimum 1, maximum 100). The floor is higher than the schema's because a
// suggestion of "3" is noise; below this the cap is not what limits the run.
const MIN_SUGGESTION = 10
const MAX_SUGGESTION = 100

function isEnabled(node: ProtocolNode): boolean {
  const config = node.data.config as unknown as Record<string, unknown> | undefined
  return config?.enabled !== false
}

/** A wired node that will cost the agent at least one tool call.
 *
 * Deliberately looser than `contextualMetricSuggestions`'s `sourceIsCallable`:
 * that one asks "is this configured well enough to produce a metric", and a
 * half-configured Script still has to be *budgeted* for, because the user will
 * finish configuring it long before they revisit this number.
 */
function connectorCost(node: ProtocolNode): number {
  if (!isEnabled(node)) return 0
  if (node.type === 'script') return SCRIPT_ITERATIONS
  if (isMcpToolNodeType(node.type)) return CONNECTOR_ITERATIONS
  if (node.type === 'skill' || node.type === 'dataset' || node.type === 'okf_bundle' || node.type === 'okf_document') {
    return CONNECTOR_ITERATIONS
  }
  return 0
}

/** The `max_iterations` this pattern node's wiring implies, or `null` when it
 * drives no agent yet.
 *
 * `max_iterations` is a **safety stop, not a budget**: a Reason+Act agent exits
 * the moment it answers, so a cap above what a run needs costs nothing, while
 * one below it silently truncates the run -- Motoro's loop keeps the last tool
 * result as the output and still marks the run `completed`
 * (`engine/runtime.py`'s `for...else`), so a cut-off run and a finished one
 * look identical downstream. That asymmetry is why this rounds up and why the
 * inspector only ever offers to *raise* the number.
 *
 * Rounded to the nearest 5 because the precision is fake -- it is a budget for
 * a loop whose length depends on what the model decides to do -- and a round
 * number reads as the estimate it is.
 */
export function suggestedMaxIterations(graph: ProtocolGraph, patternNodeId: string): number | null {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  const agentIds = graph.edges
    .filter((edge) => edge.source === patternNodeId && edge.targetHandle === 'architectural_pattern')
    .map((edge) => edge.target)
    .filter((id) => nodes.get(id)?.type === 'agent')
  if (agentIds.length === 0) return null

  // The max across agents, not the sum: each agent runs its own loop, and the
  // cap applies to each of them separately.
  const costs = agentIds.map((agentId) => {
    const wired = graph.edges
      .filter((edge) => edge.target === agentId)
      .map((edge) => nodes.get(edge.source))
      .filter((node): node is ProtocolNode => node !== undefined)
    return wired.reduce((total, node) => total + connectorCost(node), BASE_ITERATIONS)
  })

  const suggestion = Math.ceil(Math.max(...costs) / 5) * 5
  return Math.min(MAX_SUGGESTION, Math.max(MIN_SUGGESTION, suggestion))
}

/** Whether a configured cap is below what the wiring needs.
 *
 * An unset cap counts as under-iterated: an empty field is exactly the case
 * where the suggestion helps most. The two surfaces that already report "Max
 * iterations is required" separately (the node's warning triangle and the
 * pre-run scan) gate on the field being set, so neither says it twice.
 */
export function isUnderIterated(maxIterations: number | null | undefined, suggested: number | null): boolean {
  return suggested != null && (maxIterations == null || maxIterations < suggested)
}

// How far past a cap that has already failed to go. A run that died at 15
// proves 15 was short; it does not say by how much, since the loop was cut off
// before it could show us. Half again is enough headroom to finish a run that
// was close without turning a runaway loop into an expensive one -- and the
// next truncation, if there is one, raises it again from the new number.
const TRUNCATION_HEADROOM = 1.5

/** The suggestion, raised when a real run already hit this cap.
 *
 * Evidence beats the wiring heuristic: `suggestedMaxIterations` guesses from
 * what's connected, but a truncated run is the loop itself reporting that the
 * number was too small. Takes the larger of the two so a raise is never a
 * climb-down, and stays inside the catalog schema's own maximum.
 */
export function raiseForTruncation(suggested: number | null, truncatedAt: number | null | undefined): number | null {
  if (truncatedAt == null || !Number.isFinite(truncatedAt) || truncatedAt <= 0) return suggested
  const raised = Math.min(MAX_SUGGESTION, Math.ceil((truncatedAt * TRUNCATION_HEADROOM) / 5) * 5)
  return suggested == null ? raised : Math.max(suggested, raised)
}
