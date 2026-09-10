import { useRef } from 'react'
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
import { ReceivesSummary, SendsSummary } from './HandoffSummary'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { NodeRunOutputPanel, ReceivedPromptPanel, UnresolvedReferencesNote } from './NodeRunOutputPanel'
import { PromptPreviewPanel } from './PromptPreviewPanel'
import { PromptReferenceField } from './PromptReferenceField'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import { RESIZE_HANDLE_CLASSNAME, useResizablePane } from './useResizablePane'
import { experimentsApi } from '@/api/client'
import { normalizeDesignMetrics } from '@/lib/metricCatalog'
import { referenceLabel, seedPromptText } from '@/lib/promptReferences'
import type { HandoffPeers, PromptReferenceScope } from '@/lib/promptReferences'
import type { AgentNodeConfig, AgentNodeData, NodeRunState, PromptPreview, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('agent')

// The middle column is where the actual editing happens, so neither side pane
// may drag it below a width its labels and textareas still work at. Enforced
// at drag start (see useResizablePane) against the frame's measured width.
const MIN_PARAMETERS_WIDTH = 380
// The two 16px handles plus the padding either side of each -- the layout cost
// of the gutters, which the panes themselves don't get to spend.
const GUTTER_WIDTH = 96

// A node's setup opens as a large centered floating window over the dimmed
// canvas, not a sidebar or an edge-to-edge takeover.
// Built on this app's own Dialog primitives (base-ui) rather than
// a hand-rolled overlay -- Escape-to-close, backdrop-click-to-close, focus
// trapping, and body scroll lock all come for free from `modal` (default
// true), instead of reimplementing them. Sizing (fixed, near-fullscreen,
// unaffected by which Parameters/Settings tab is active) is shared with the
// other node inspectors via `NodeInspectorDialog` -- see that file for why.
//
// Three columns: Input, then Parameters/Settings, then Output -- laid out in
// the direction data actually travels, so the agent's configuration sits
// literally between what it is handed and what it produces.
//
// Input and Output are always-visible panes rather than tabs because both are
// things you check *while* adjusting Parameters, not destinations you tab away
// to and lose your editing context to get to. Both are drag-resizable and
// remember their width: how much room the evidence deserves against the form
// depends on whether you're building a prompt or reading a run, and that
// changes minute to minute.
//
// The split also decides where the handoff readout goes. Receives heads Input
// and Sends heads Output, each above the data it describes, and "the prompt
// this agent received" is input -- so on a run that already happened it appears
// on the left, not buried under the output it produced.
//
// Parameters/Settings (middle, tabbed) splits what defines the agent's
// behavior/identity from what constrains its execution.
export function AgentNodeInspector({
  node,
  experimentId,
  markedLeadAgentId,
  referenceScope,
  handoffPeers,
  wiredOutputParserLabel,
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
  // The label of the Output Parser node wired into this agent, or null if
  // none is. Same reasoning as markedLeadAgentId: it's wiring, which only the
  // canvas can see.
  wiredOutputParserLabel: string | null
  // Assembles the real prompt server-side against the live canvas. A callback
  // rather than an id pair because the graph it posts is the unsaved one on
  // screen, which only ProtocolCanvas holds.
  fetchPromptPreview: (nodeId: string) => Promise<PromptPreview>
  nodeRun?: NodeRunState
  onChange: (nodeId: string, data: AgentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const { requestMakeFactor, requestConnectorAdd, convertLegacyOutputContract } = useProtocolCanvasActions()
  // Measured at drag start so each pane's ceiling accounts for what the other
  // one is currently taking; read through a ref because the two hooks below
  // would otherwise have to reference each other's not-yet-declared width.
  const columnsRef = useRef<HTMLDivElement>(null)
  const widthsRef = useRef({ input: 0, output: 0 })
  const roomFor = (other: 'input' | 'output') =>
    (columnsRef.current?.clientWidth ?? Number.POSITIVE_INFINITY) - widthsRef.current[other] - MIN_PARAMETERS_WIDTH - GUTTER_WIDTH

  const inputPane = useResizablePane({
    storageKey: 'asaree:agent-input-pane-width',
    defaultWidth: 340,
    minWidth: 260,
    maxWidth: 700,
    side: 'left',
    resolveMaxWidth: () => roomFor('output'),
    recomputeKey: node?.id ?? '',
  })
  const outputPane = useResizablePane({
    storageKey: 'asaree:agent-output-pane-width',
    defaultWidth: 384,
    minWidth: 280,
    maxWidth: 760,
    side: 'right',
    resolveMaxWidth: () => roomFor('input'),
    recomputeKey: node?.id ?? '',
  })
  widthsRef.current = { input: inputPane.width, output: outputPane.width }

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
  // The pre-node way of declaring an output shape, still honoured by the
  // executor when no parser node is wired (see _resolve_output_contract).
  // Its presence swaps the section below into the convert-it banner: an agent
  // is never allowed to have both at once.
  const legacyContract = config.output_contract
  // A wired parser is itself the requirement, whether or not the flag was ever
  // set: the flag only ever existed to keep the connector drawn while the node
  // it is waiting for does not exist yet.
  const parserWired = wiredOutputParserLabel !== null
  const formatRequired = parserWired || config.require_output_parser === true

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
      <div ref={columnsRef} className="flex h-full">
        <div className="min-w-0 shrink-0 space-y-3 overflow-y-auto pr-4" style={{ width: inputPane.width }}>
          <p className="text-sm font-semibold">Input</p>
          {/* First, because it is the context everything below is read
              against: every incoming edge delivers, so "what am I even given?"
              has to be answerable before the assembled prompt underneath means
              anything. */}
          <ReceivesSummary peers={handoffPeers} prompt={seedPromptText(node)} />
          <PromptPreviewPanel
            // The panel holds the last text it assembled; on a node switch
            // that text describes the previous node, so it starts over.
            key={node.id}
            signature={JSON.stringify(data)}
            fetchPreview={() => fetchPromptPreview(node.id)}
          />
          {/* Below the design-time preview, because it supersedes it: a
              placeholder proves nothing about a run that actually happened. */}
          {nodeRun?.run_id && (
            // Boxed to match the preview above it -- in this pane the two are a
            // matched pair, where elsewhere the panel is one item in a list.
            <div className="rounded-md border bg-muted/20 p-3">
              <ReceivedPromptPanel runId={nodeRun.run_id} />
            </div>
          )}
          <UnresolvedReferencesNote
            names={(nodeRun?.unresolved_references ?? []).map((ref) => referenceLabel(ref, referenceScope.names))}
          />
        </div>

        <div
          role="separator"
          aria-label="Resize input panel"
          aria-orientation="vertical"
          title="Drag to resize input panel"
          className={RESIZE_HANDLE_CLASSNAME}
          {...inputPane.handleProps}
        />

        <div className="mx-4 min-w-0 flex-1 overflow-y-auto">
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

              {/* Directly under the prompt, because "what to write" and "what
                  shape to write it in" are one decision. There used to be two
                  controls here -- a free-text Expected output field and this
                  box -- which made the user keep two descriptions of one answer
                  in agreement by hand. Now there is one switch: off means
                  answer in prose, on means take the shape the wired Output
                  Parser declares. Not a tab away: splitting the shape off into
                  Settings is what made the old stored contract invisible. */}
              <div className="space-y-2 rounded-md border bg-muted/20 p-3">
                <Label className="text-sm">Output format</Label>
                {legacyContract ? (
                  // Read-only on purpose: this contract is still live (the
                  // executor falls back to it whenever no parser node is
                  // wired), but it is no longer editable here -- the number
                  // of agents carrying one can only go down. Editing means
                  // converting first.
                  <>
                    <p className="text-xs text-muted-foreground">
                      This agent carries an output contract stored on the node itself, from before parsers were
                      canvas nodes. It still runs, and it shapes the agent's answer the same way a wired parser
                      would — but it is invisible on the canvas, and nothing on the canvas says it is there.
                      Convert it to see and edit it.
                    </p>
                    <div className="rounded border bg-background/60 p-2 font-mono text-[11px]">
                      <div className="text-muted-foreground">{legacyContract.name || '(unnamed)'}</div>
                      {(legacyContract.fields ?? []).map((field, i) => (
                        <div key={i} className="truncate">
                          {field.name || '(unnamed)'}
                          <span className="text-muted-foreground"> ({field.type})</span>
                        </div>
                      ))}
                    </div>
                    <Button size="sm" variant="outline" onClick={() => convertLegacyOutputContract(node.id)}>
                      Convert to an Output Parser node
                    </Button>
                  </>
                ) : (
                  <>
                    {/* Checked-and-disabled whenever a parser is already wired:
                        a graph can arrive with the edge but not the flag (the
                        SDK sets no flag, and the connector is drawn from the
                        edge regardless -- see AgentNode's showOutputParser), and
                        an unchecked box above a connected parser would be a
                        lie. Turning the requirement off then means removing the
                        node, which is where the shape actually lives. */}
                    <label
                      htmlFor={`require-output-parser-${node.id}`}
                      className={`flex items-center gap-2 rounded px-1 py-1 text-xs ${
                        parserWired ? 'cursor-default' : 'cursor-pointer hover:bg-muted/50'
                      }`}
                    >
                      <Checkbox
                        id={`require-output-parser-${node.id}`}
                        checked={formatRequired}
                        disabled={parserWired}
                        onCheckedChange={(checked) => patchConfig({ require_output_parser: checked === true })}
                      />
                      Require specific output format
                    </label>
                    {!formatRequired ? (
                      <p className="text-xs text-muted-foreground">
                        This agent answers in whatever prose the model produces. Nothing states a shape to it, and
                        nothing reads named values back out.
                      </p>
                    ) : parserWired ? (
                      <p className="text-xs text-muted-foreground">
                        <span className="font-medium text-foreground">{wiredOutputParserLabel || 'Output Parser'}</span>{' '}
                        defines the format — it tells this agent which fields to state, and reads them back out of the
                        answer. Open it on the canvas to edit them. Remove it to stop requiring a format.
                      </p>
                    ) : (
                      <>
                        <p className="text-xs text-[color:var(--chart-4)]">
                          Connect an Output Parser node to define the format.
                        </p>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => requestConnectorAdd({ nodeId: node.id, slot: 'output_parser' })}
                        >
                          Connect an Output Parser
                        </Button>
                      </>
                    )}
                  </>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="node-goal">Goal — Optional</Label>
                <Textarea id="node-goal" rows={2} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
                <p className="text-xs text-muted-foreground">
                  A persistent, one-line objective for this agent -- distinct from Prompt (this run's specific
                  ask) and System prompt (detailed behavioral instructions). Doubles as this agent's default
                  Prompt when one isn't given.
                </p>
              </div>

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
                  <PromptReferenceField
                    // Same remount-on-node-switch reason as Prompt above: the
                    // field holds a local display draft keyed to one node.
                    key={node.id}
                    id="node-system-prompt"
                    label="System prompt — Optional"
                    trigger={trigger}
                    rows={6}
                    className="font-mono text-xs"
                    value={config.system_prompt}
                    scope={referenceScope}
                    placeholder={defaultSystemPrompt(data.label, 'Agent')}
                    onChange={(system_prompt) => patchConfig({ system_prompt })}
                    description={
                      <>
                        Behavioral instructions layered on top of the Reason + Act pattern's own built-in system
                        prompt. Leave blank to use the explicit default shown above instead (this agent's own canvas
                        label, not a bare/uninstructed mode). References resolve here the same way they do in Prompt.
                      </>
                    }
                  />
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

            </TabsContent>
          </Tabs>
        </div>

        <div
          role="separator"
          aria-label="Resize output panel"
          aria-orientation="vertical"
          title="Drag to resize output panel"
          className={RESIZE_HANDLE_CLASSNAME}
          {...outputPane.handleProps}
        />

        <div className="min-w-0 shrink-0 space-y-3 overflow-y-auto pl-4" style={{ width: outputPane.width }}>
          <p className="text-sm font-semibold">Output</p>
          {/* Mirroring Receives on the far side: who this answer is handed to,
              stated above the answer itself. */}
          <SendsSummary peers={handoffPeers} />
          {/* The received prompt lives in the Input pane instead -- see the
              layout note at the top of this file. */}
          <NodeRunOutputPanel nodeRun={nodeRun} referenceNames={referenceScope.names} showReceivedPrompt={false} />
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
