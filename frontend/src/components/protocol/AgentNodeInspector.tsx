import { Bot } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { hashToChartHue } from '@/lib/utils'
import { defaultSystemPrompt } from './defaultSystemPrompt'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField, MakeNodeFactorButton } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { NodeRunOutputPanel } from './NodeRunOutputPanel'
import { OutputContractEditor } from './OutputContractEditor'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import type { AgentNodeConfig, AgentNodeData, NodeRunState, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('agent')

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
  nodeRun,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: AgentNodeData }) | null
  experimentId: string | null
  nodeRun?: NodeRunState
  onChange: (nodeId: string, data: AgentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const { requestMakeFactor } = useProtocolCanvasActions()

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

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
      <div className="flex h-full gap-4">
        <div className="min-w-0 flex-1 overflow-y-auto">
          <Tabs defaultValue="parameters">
            <TabsList>
              <TabsTrigger value="parameters">Parameters</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
            </TabsList>

            <TabsContent value="parameters" className="space-y-4 pt-2">
              <div className="space-y-1.5">
                <Label htmlFor="node-prompt">Prompt (User Message) — Optional</Label>
                <Textarea id="node-prompt" rows={2} value={config.prompt} onChange={(e) => patchConfig({ prompt: e.target.value })} />
                <p className="text-xs text-muted-foreground">
                  The task for this run -- what you're actually asking this agent to do. Leave blank to fall back
                  to Goal (or, once wired to an earlier step, that step's own output).
                </p>
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

        <div className="w-96 shrink-0 space-y-3 overflow-y-auto border-l pl-4">
          <p className="text-sm font-semibold">Output</p>
          <NodeRunOutputPanel nodeRun={nodeRun} />
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
