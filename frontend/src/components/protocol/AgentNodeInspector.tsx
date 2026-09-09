import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { Bot } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { defaultSystemPrompt } from './defaultSystemPrompt'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField, MakeNodeFactorButton } from './FactorBindableField'
import { HandoffSummary } from './HandoffSummary'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { NodeRunOutputPanel } from './NodeRunOutputPanel'
import { OutputContractEditor } from './OutputContractEditor'
import { PromptPreviewPanel } from './PromptPreviewPanel'
import { PromptReferenceField } from './PromptReferenceField'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import { experimentsApi } from '@/api/client'
import { normalizeDesignMetrics } from '@/lib/metricCatalog'
import { seedPromptText } from '@/lib/promptReferences'
import type { HandoffPeers, PromptReferenceScope } from '@/lib/promptReferences'
import type { AgentNodeConfig, AgentNodeData, NodeRunState, PromptPreview, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('agent')
const DEFAULT_OUTPUT_PANE_WIDTH = 384
const MIN_OUTPUT_PANE_WIDTH = 280
const MAX_OUTPUT_PANE_WIDTH = 760
const OUTPUT_PANE_WIDTH_STORAGE_KEY = 'asaree:agent-output-pane-width'

function outputPaneWidth(): number {
  const raw = typeof window !== 'undefined' ? Number(window.localStorage.getItem(OUTPUT_PANE_WIDTH_STORAGE_KEY)) : NaN
  return Number.isFinite(raw) ? Math.min(MAX_OUTPUT_PANE_WIDTH, Math.max(MIN_OUTPUT_PANE_WIDTH, raw)) : DEFAULT_OUTPUT_PANE_WIDTH
}

// A node's setup opens as a large centered floating window over the dimmed
// canvas, not a sidebar or an edge-to-edge takeover.
// Built on this app's own Dialog primitives (base-ui) rather than
// a hand-rolled overlay -- Escape-to-close, backdrop-click-to-close, focus
// trapping, and body scroll lock all come for free from `modal` (default
// true), instead of reimplementing them. Sizing (fixed, near-fullscreen,
// unaffected by which Parameters/Settings tab is active) is shared with the
// other node inspectors via `NodeInspectorDialog` -- see that file for why.
//
// Parameters/Settings (left, tabbed) splits what defines the agent's
// behavior/identity from what constrains its execution. Output is a
// right-hand side pane (always visible, not a third tab) instead -- run
// results are something you check
// *while* adjusting Parameters, not a destination you tab away to and lose
// your editing context to get to. See NodeRunOutputPanel.
export function AgentNodeInspector({
  node,
  experimentId,
  markedLeadAgentId,
  referenceScope,
  handoffPeers,
  fetchPromptPreview,
  nodeRun,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: AgentNodeData }) | null
  experimentId: string | null
  // Which agent on the canvas already carries the lead marker, if any -- the
  // inspector can't see its siblings, so ProtocolCanvas resolves it.
  markedLeadAgentId: string | null
  // What this node's prompt may reference, resolved from the graph for the same
  // reason as markedLeadAgentId: the inspector only ever sees its own node.
  referenceScope: PromptReferenceScope
  // Who hands off to this node and who it hands off to. Same reasoning again --
  // it's the wiring around the node, which only the canvas can see.
  handoffPeers: HandoffPeers
  // Assembles the real prompt server-side against the live canvas. A callback
  // rather than an id pair because the graph it posts is the unsaved one on
  // screen, which only ProtocolCanvas holds.
  fetchPromptPreview: (nodeId: string) => Promise<PromptPreview>
  nodeRun?: NodeRunState
  onChange: (nodeId: string, data: AgentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const { requestMakeFactor } = useProtocolCanvasActions()
  const [outputWidth, setOutputWidth] = useState(outputPaneWidth)
  const [resizingOutput, setResizingOutput] = useState(false)
  const outputDragStart = useRef<{ x: number; width: number } | null>(null)

  useEffect(() => {
    if (!resizingOutput) return
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
  }, [resizingOutput])

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })

  // Derived above the `!node` bail-out because the query below is a hook: it has
  // to run on every render, including the no-selection one, where it's disabled.
  const metrics = normalizeDesignMetrics(experimentQuery.data?.design_spec?.metrics)
  const validMetricIds = new Set(metrics.map((metric) => metric.id!))
  const contextMetricIds = (node?.data.contextMetricIds ?? []).filter((id) => validMetricIds.has(id))
  const evaluationContextQuery = useQuery({
    queryKey: ['experiments', experimentId, 'evaluation-context', contextMetricIds],
    queryFn: () => experimentsApi.evaluationContext(experimentId!, contextMetricIds),
    enabled: !!experimentId && contextMetricIds.length > 0,
  })
  const evaluationContext = evaluationContextQuery.data?.context ?? ''

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  // The lead marker is meaningless under any other coordination strategy, so
  // it isn't offered under one -- a checkbox that does nothing on the
  // overwhelmingly common single-agent/sequential experiment is worse than an
  // absent one. An already-marked agent still keeps its flag through a strategy
  // change; nothing reads it, and switching back shouldn't silently lose it.
  // Both strategies read the SAME `conversation_lead` field, deliberately: it
  // marks "the agent this strategy hands the task to", which is the lead under
  // one and the supervisor under the other, so the two never disagree about
  // who that is and switching strategy doesn't need a re-mark.
  const strategySlug = experimentQuery.data?.design_spec?.coordination_strategy?.slug
  const leadRole = strategySlug === 'peer_collaboration' ? 'lead' : strategySlug === 'supervisor_architecture' ? 'supervisor' : null
  // Exactly one agent can lead (two is a server-side validation error), so once
  // one is marked the checkbox is offered on that agent alone -- unmark it there
  // to move the role. Showing it everywhere would invite creating an invalid
  // canvas, and silently reassigning on click would move a role the user might
  // only have been inspecting.
  const canMarkLead = leadRole !== null && (markedLeadAgentId === null || markedLeadAgentId === node.id)

  function patchConfig(patch: Partial<AgentNodeConfig>) {
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

  function patchContextMetricIds(next: string[]) {
    // Stale references disappear as soon as the node is next saved; the
    // executor also filters them defensively, so a removed Design metric can
    // never crash a run or leak old context.
    onChange(node!.id, { ...data, contextMetricIds: next.filter((id) => validMetricIds.has(id)) })
  }

  function startOutputResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId)
    outputDragStart.current = { x: event.clientX, width: outputWidth }
    setResizingOutput(true)
  }

  function moveOutputResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!outputDragStart.current) return
    setOutputWidth(Math.round(Math.min(MAX_OUTPUT_PANE_WIDTH, Math.max(MIN_OUTPUT_PANE_WIDTH, outputDragStart.current.width + outputDragStart.current.x - event.clientX))))
  }

  function endOutputResize() {
    if (!outputDragStart.current) return
    outputDragStart.current = null
    setResizingOutput(false)
    window.localStorage.setItem(OUTPUT_PANE_WIDTH_STORAGE_KEY, String(outputWidth))
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
          <Bot className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Agent" onCommit={(label) => onChange(node.id, { ...data, label })} />
          <MakeNodeFactorButton onClick={() => requestMakeFactor(node.id)} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="flex h-full">
        <div className="mr-4 min-w-0 flex-1 overflow-y-auto">
          <Tabs defaultValue="parameters">
            <TabsList>
              <TabsTrigger value="parameters">Parameters</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>

            <TabsContent value="parameters" className="space-y-4 pt-2">
              {/* First, above Prompt: which agent leads decides whose prompt
                  becomes the task and whose answer gets scored, so it frames
                  everything below it rather than being one more setting. It
                  only renders under the two strategies that read the marker,
                  and only on the agent that may still take the role (see
                  canMarkLead), so it costs the common single-agent case no
                  vertical space at all. */}
              {canMarkLead && (
                <div className="space-y-2 rounded-md border bg-muted/20 p-3">
                  <label
                    htmlFor={`conversation-lead-${node.id}`}
                    className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-muted/50"
                  >
                    <Checkbox
                      id={`conversation-lead-${node.id}`}
                      checked={data.conversation_lead === true}
                      onCheckedChange={(checked) => onChange(node.id, { ...data, conversation_lead: checked === true })}
                    />
                    <span className="min-w-0">
                      <span className="text-sm font-medium">
                        {leadRole === 'supervisor' ? 'Supervisor' : 'Conversation lead'}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {leadRole === 'supervisor' ? (
                          <>
                            This experiment coordinates by Supervisor, so the task goes to one agent, which briefs the
                            agents it hands off to and then writes the final answer from what they report back. That
                            answer is what gets recorded and scored. Leave this off to pick the supervisor from the
                            wiring instead -- the agent that hands off to others and nothing feeds into.
                          </>
                        ) : (
                          <>
                            This experiment coordinates by Peer Collaboration, so the task goes to one agent, which can
                            consult the peers it's wired to while it works. That agent's answer is what gets recorded
                            and scored. Leave this off to pick the lead from the wiring instead -- the connected agent
                            nothing feeds into. Mark it when the agents are wired in a loop, where there is no such
                            agent.
                          </>
                        )}
                      </span>
                    </span>
                  </label>
                </div>
              )}

              {/* Above the prompt, because it is the context the prompt is
                  written against: an edge grants availability and a reference
                  grants use, so "what am I even given?" has to be answerable
                  before the sentence referencing it makes sense. */}
              <HandoffSummary peers={handoffPeers} prompt={seedPromptText(node)} />

              <FactorBindableField
                experimentId={experimentId}
                nodeId={node.id}
                fieldPath="config.prompt"
                defaultLabel="Prompt"
                nodeLabel={data.label || 'Agent'}
                levelType="text"
                currentValue={config.prompt}
                boundFactorName={bindings['config.prompt']}
                onBind={(name) => bindFactor('config.prompt', name)}
                onUnbind={() => unbindFactor('config.prompt')}
              >
                {(trigger) => (
                  <PromptReferenceField
                    // Remount on a node switch: the field keeps a local display
                    // draft, which must be re-derived from the node now being
                    // edited rather than carried over from the last one.
                    key={node.id}
                    id="node-prompt"
                    label="Prompt (User Message) — Optional"
                    trigger={trigger}
                    rows={4}
                    value={config.prompt}
                    scope={referenceScope}
                    onChange={(prompt) => patchConfig({ prompt })}
                    description={
                      <>
                        The task for this run -- what you're actually asking this agent to do. An earlier step's output
                        reaches this agent only where you reference it, so wiring alone passes nothing. Leave blank to
                        fall back to Goal.
                      </>
                    }
                  />
                )}
              </FactorBindableField>

              <div className="space-y-1.5">
                <Label htmlFor="node-goal">Goal — Optional</Label>
                <Textarea id="node-goal" rows={2} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
                <p className="text-xs text-muted-foreground">
                  A persistent, one-line objective for this agent -- distinct from Prompt (this run's specific
                  ask) and System prompt (detailed behavioral instructions). Doubles as this agent's default
                  Prompt when one isn't given.
                </p>
              </div>

              {/* After Goal, not between it and Prompt: the preview resolves
                  Prompt-falling-back-to-Goal, so it only tells the whole truth
                  once both fields are above it. */}
              <PromptPreviewPanel
                // The panel holds the last text it assembled; on a node switch
                // that text describes the previous node, so it starts over.
                key={node.id}
                signature={JSON.stringify(data)}
                fetchPreview={() => fetchPromptPreview(node.id)}
              />

              <div className="space-y-1.5">
                <Label htmlFor="node-description">Description — Optional</Label>
                <Textarea
                  id="node-description"
                  rows={2}
                  value={config.description}
                  onChange={(e) => patchConfig({ description: e.target.value })}
                />
              </div>

              <FactorBindableField
                experimentId={experimentId}
                fieldPath="config.system_prompt"
                defaultLabel="System prompt"
                nodeLabel={data.label || 'Agent'}
                levelType="text"
                currentValue={config.system_prompt}
                boundFactorName={bindings['config.system_prompt']}
                onBind={(name) => bindFactor('config.system_prompt', name)}
                onUnbind={() => unbindFactor('config.system_prompt')}
              >
                {(trigger) => (
                  <div className="w-full space-y-1.5">
                    <Label htmlFor="node-system-prompt" className="flex items-center gap-1.5">
                      System prompt — Optional
                      {trigger}
                    </Label>
                    <Textarea
                      id="node-system-prompt"
                      rows={6}
                      className="font-mono text-xs"
                      value={config.system_prompt}
                      onChange={(e) => patchConfig({ system_prompt: e.target.value })}
                      placeholder={defaultSystemPrompt(data.label, 'Agent')}
                    />
                    <p className="text-xs text-muted-foreground">
                      Behavioral instructions layered on top of the Reason + Act pattern's own built-in system
                      prompt. Leave blank to use the explicit default shown above instead (this agent's own canvas
                      label, not a bare/uninstructed mode).
                    </p>
                  </div>
                )}
              </FactorBindableField>

              <div className="space-y-2 rounded-md border bg-muted/20 p-3">
                <div>
                  <Label className="text-sm">Evaluation context</Label>
                  <p className="mt-1 text-xs text-muted-foreground">The Agent receives the selected metric definitions and optimization direction. Metric results are calculated or recorded after the run.</p>
                </div>
                {metrics.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No experiment metrics are available. Add metrics in Design.</p>
                ) : (
                  <>
                    <div className="flex gap-2"><Button variant="ghost" size="sm" onClick={() => patchContextMetricIds(metrics.map((metric) => metric.id!))}>Select all</Button><Button variant="ghost" size="sm" onClick={() => patchContextMetricIds([])}>Clear</Button></div>
                    <div className="space-y-2">
                      {metrics.map((metric) => {
                        const checkboxId = `context-metric-${node.id}-${metric.id}`
                        return <label key={metric.id} htmlFor={checkboxId} className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 hover:bg-muted/50">
                          <Checkbox id={checkboxId} checked={contextMetricIds.includes(metric.id!)} onCheckedChange={(checked) => patchContextMetricIds(checked ? [...contextMetricIds, metric.id!] : contextMetricIds.filter((id) => id !== metric.id))} />
                          <span className="min-w-0"><span className="text-sm font-medium">{metric.name} · {metric.direction}</span><span className="mt-0.5 block text-xs text-muted-foreground">{metric.description}{metric.kind === 'runtime' ? ' Final value is available only after execution.' : ''}</span><span className="sr-only">Include {metric.name} in this Agent's context</span></span>
                        </label>
                      })}
                    </div>
                    {evaluationContext && <details className="text-xs"><summary className="cursor-pointer text-muted-foreground hover:text-foreground">Preview generated context</summary><pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap rounded bg-background p-2 font-mono text-[11px]">{evaluationContext}</pre></details>}
                  </>
                )}
              </div>
            </TabsContent>

            <TabsContent value="settings" className="space-y-4 pt-2">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="node-budget">Budget (USD)</Label>
                  <Input
                    id="node-budget"
                    type="number"
                    min="0"
                    value={config.budget_limit_usd ?? ''}
                    onChange={(e) => patchConfig({ budget_limit_usd: e.target.value === '' ? null : Number(e.target.value) })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="node-duration">Max duration (s)</Label>
                  <Input
                    id="node-duration"
                    type="number"
                    min="0"
                    value={config.max_run_duration_seconds ?? ''}
                    onChange={(e) => patchConfig({ max_run_duration_seconds: e.target.value === '' ? null : Number(e.target.value) })}
                  />
                </div>
              </div>

              <OutputContractEditor value={config.output_contract} onChange={(next) => patchConfig({ output_contract: next })} />
            </TabsContent>
          </Tabs>
        </div>

        <div
          role="separator"
          aria-label="Resize output panel"
          aria-orientation="vertical"
          title="Drag to resize output panel"
          className="relative w-4 shrink-0 cursor-col-resize touch-none rounded hover:bg-primary/10 before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-border hover:before:bg-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onPointerDown={startOutputResize}
          onPointerMove={moveOutputResize}
          onPointerUp={endOutputResize}
          onPointerCancel={endOutputResize}
        />
        <div className="shrink-0 space-y-3 overflow-y-auto pl-4" style={{ width: outputWidth }}>
          <p className="text-sm font-semibold">Output</p>
          <NodeRunOutputPanel nodeRun={nodeRun} referenceNames={referenceScope.names} />
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
