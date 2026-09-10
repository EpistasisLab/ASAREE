import { Braces } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { OutputContractEditor } from './OutputContractEditor'
import type { OutputParserNodeConfig, OutputParserNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('output_parser')

// Home of the field-spec editor that used to sit in the Agent inspector's
// Settings tab. Moving it here is the point of the node: the shape an agent's
// answer must take is now a thing on the canvas with an edge, not a switch
// buried two tabs deep in whichever agent happens to own it.
export function OutputParserNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: OutputParserNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's agentTracedLabel)
  // -- distinct from data.label, which is this node's own plain label shown in
  // the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: OutputParserNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<OutputParserNodeConfig>) {
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
          <Braces className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle
            label={data.label}
            placeholder="Output Parser"
            onCommit={(label) => onChange(node.id, { ...data, label })}
          />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      {/* Says the cost out loud -- it's the one thing about this node a user
          can't see from the canvas. It used to read "a second model call per
          run"; that is now the exception rather than the rule, and the
          fallback is worth naming because it's what a caveat on a finished run
          is telling you about. */}
      <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        The connected agent is told to state these values and to repeat them as JSON at the end of its answer, which
        is read back with no extra model call. A run whose answer arrives without that block falls back to a second
        model call to extract them, and says so in its caveats.
      </div>
      {/* Bindable as a plain boolean, like Memory's and a Tool node's:
          "structured output vs. prose" is a legitimate treatment to compare
          across cells, and it's the only way to A/B the extra call. */}
      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.enabled"
        defaultLabel="Enabled"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.enabled']}
        onBind={(name) => bindFactor('config.enabled', name)}
        onUnbind={() => unbindFactor('config.enabled')}
      >
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <Label htmlFor="output-parser-enabled" className="flex items-center gap-1.5">
              Enabled
              {trigger}
            </Label>
            <Switch
              id="output-parser-enabled"
              checked={config.enabled ?? true}
              onCheckedChange={(checked) => patchConfig({ enabled: checked })}
            />
          </div>
        )}
      </FactorBindableField>
      <OutputContractEditor
        value={config.output_contract}
        onChange={(next) => patchConfig({ output_contract: next })}
      />
    </NodeInspectorDialog>
  )
}
