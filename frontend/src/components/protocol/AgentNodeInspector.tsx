import { Bot, Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { OutputContractEditor } from './OutputContractEditor'
import type { AgentNodeConfig, AgentNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('agent')

// n8n opens a node's setup as a large centered floating window over the
// dimmed canvas (its Node Detail View), not a sidebar or an edge-to-edge
// takeover. Built on this app's own Dialog primitives (base-ui) rather than
// a hand-rolled overlay -- Escape-to-close, backdrop-click-to-close, focus
// trapping, and body scroll lock all come for free from `modal` (default
// true), instead of reimplementing them. Sizing (fixed, near-fullscreen,
// unaffected by which Parameters/Settings tab is active) is shared with the
// other node inspectors via `NodeInspectorDialog` -- see that file for why.
//
// Parameters/Settings mirrors n8n's own NDV split -- what defines the
// agent's behavior/identity vs. what constrains its execution -- but drops
// n8n's INPUT/OUTPUT panes: those show a node's actual run data, which only
// exists once ASAREE has an execution engine (not yet built). Adding empty
// IN/OUT panes now would be UI with nothing behind it, same reasoning that
// kept unbuilt node types out of the palette.
export function AgentNodeInspector({
  node,
  experimentId,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: AgentNodeData }) | null
  experimentId: string | null
  onChange: (nodeId: string, data: AgentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
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
      header={
        <>
          <div className="flex items-center gap-2">
            <Bot className="size-5" style={{ color: ACCENT }} />
            <EditableNodeTitle label={data.label} placeholder="Agent" onCommit={(label) => onChange(node.id, { ...data, label })} />
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
      <Tabs defaultValue="parameters">
          <TabsList>
            <TabsTrigger value="parameters">Parameters</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          <TabsContent value="parameters" className="space-y-4 pt-2">
            <div className="space-y-1.5">
              <Label htmlFor="node-goal">Goal</Label>
              <Textarea id="node-goal" rows={2} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="node-description">Description (Optional)</Label>
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
              levelType="string"
              boundFactorName={bindings['config.system_prompt']}
              onBind={(name) => bindFactor('config.system_prompt', name)}
              onUnbind={() => unbindFactor('config.system_prompt')}
            >
              <div className="w-full space-y-1.5">
                <Label htmlFor="node-system-prompt">System prompt</Label>
                <Textarea
                  id="node-system-prompt"
                  rows={6}
                  className="font-mono text-xs"
                  value={config.system_prompt}
                  onChange={(e) => patchConfig({ system_prompt: e.target.value })}
                />
              </div>
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
    </NodeInspectorDialog>
  )
}
