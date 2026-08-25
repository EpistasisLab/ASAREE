import { Code2 } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PythonCodeEditor } from './PythonCodeEditor'
import type { ScriptNodeConfig, ScriptNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('script')

// Same floating-dialog shell as every other node inspector. Python-only for
// v1 (see ScriptNodeData's own comment in types/protocols.ts) -- "Language"
// is a fixed label, not a picker, so there's nothing to configure there yet.
// The whole node is also factor-bindable (bindableFields.ts's 'script_config'
// kind) -- comparing two hand-written scoring scripts as an experimental
// factor is a direct use of the same whole-node-config mechanism llm_config/
// tool_config/pattern already have.
export function ScriptNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: ScriptNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's
  // agentTracedLabel) -- distinct from data.label, which is this node's own
  // plain label shown in the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: ScriptNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<ScriptNodeConfig>) {
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
          <Code2 className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Script" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="script-name">Name</Label>
          <Input id="script-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label>Language</Label>
          <p className="rounded-md border border-dashed px-2.5 py-1.5 text-sm text-muted-foreground">Python</p>
        </div>
      </div>

      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config"
        defaultLabel="Script"
        nodeLabel={factorNodeLabel}
        levelType="script_config"
        currentValue={config}
        boundFactorName={bindings.config}
        onBind={(name) => bindFactor('config', name)}
        onUnbind={() => unbindFactor('config')}
      >
        {(trigger) => (
          <div className="w-full space-y-1.5">
            <Label className="flex items-center gap-1.5">
              Code
              {trigger}
            </Label>
            <PythonCodeEditor value={config.code} onChange={(code) => patchConfig({ code })} rows={16} />
          </div>
        )}
      </FactorBindableField>
    </NodeInspectorDialog>
  )
}
