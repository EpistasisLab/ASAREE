import { ShieldCheck } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { CriticGateNodeConfig, CriticGateNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('critic_gate')

// Same fixed-size NodeInspectorDialog shell as AgentNodeInspector/
// McpToolNodeInspector (see that file), but with a deliberately smaller
// field set: the critic never gets tools, always runs single-pass, and its
// output_contract is fixed by the executor (CRITIC_OUTPUT_CONTRACT) --
// none of that belongs in this UI at all, unlike an Agent node's much
// larger config surface. Fewer fields no longer means a smaller dialog,
// though -- the frame is fixed regardless, only the scrollable body inside
// it is shorter.
export function CriticGateNodeInspector({
  node,
  experimentId,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: CriticGateNodeData }) | null
  experimentId: string | null
  onChange: (nodeId: string, data: CriticGateNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<CriticGateNodeConfig>) {
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
          <ShieldCheck className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Critic Gate" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.enabled"
          defaultLabel="Critic enabled"
          nodeLabel={data.label || 'Critic Gate'}
          levelType="boolean"
          boundFactorName={bindings['config.enabled']}
          onBind={(name) => bindFactor('config.enabled', name)}
          onUnbind={() => unbindFactor('config.enabled')}
        >
          {(trigger) => (
            <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
              <div>
                <Label htmlFor="gate-enabled" className="flex items-center gap-1.5">
                  Enabled
                  {trigger}
                </Label>
                <p className="text-xs text-muted-foreground">
                  Off: the upstream agent's output passes straight through, no review, no revisions.
                </p>
              </div>
              <Switch id="gate-enabled" checked={config.enabled} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
            </div>
          )}
        </FactorBindableField>

        <div className="space-y-1.5">
          <Label htmlFor="gate-name">Name</Label>
          <Input id="gate-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-goal">Review instructions</Label>
          <Textarea id="gate-goal" rows={3} value={config.goal} onChange={(e) => patchConfig({ goal: e.target.value })} />
          <p className="text-xs text-muted-foreground">
            What this critic checks for -- becomes its goal. It always returns a structured
            approved/feedback/rejection_scope verdict; that part isn't editable.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-system-prompt">System prompt</Label>
          <Textarea
            id="gate-system-prompt"
            rows={4}
            className="font-mono text-xs"
            value={config.system_prompt}
            onChange={(e) => patchConfig({ system_prompt: e.target.value })}
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="gate-max-revisions">Max revisions</Label>
          <Input
            id="gate-max-revisions"
            type="number"
            min="0"
            value={config.max_revisions}
            onChange={(e) => patchConfig({ max_revisions: Number(e.target.value) })}
          />
        </div>
    </NodeInspectorDialog>
  )
}
