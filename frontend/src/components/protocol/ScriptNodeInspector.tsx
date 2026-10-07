import { Code2, Power, Split, Trash2 } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { EditableNodeTitle } from './EditableNodeTitle'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PythonCodeEditor } from './PythonCodeEditor'
import { useProtocolCanvasActions } from './ProtocolCanvasContext'
import type { ScriptNodeConfig, ScriptNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('script')

// Same floating-dialog shell as every other node inspector. Python-only for
// v1 (see ScriptNodeData's own comment in types/protocols.ts) -- "Language"
// is a fixed label, not a picker, so there's nothing to configure there yet.
// Script factor creation lives in the hover toolbar. The inspector edits the
// base code and exposes existing factor links and connector-controlled availability.
export function ScriptNodeInspector({
  node,
  connectorFactorName,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: ScriptNodeData }) | null
  connectorFactorName?: string
  onChange: (nodeId: string, data: ScriptNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const { requestEditFactor } = useProtocolCanvasActions()
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<ScriptNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
      <div className="rounded-lg border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Script node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon.
          {' '}<span className="ml-4 inline-flex flex-col gap-1 align-middle">
            <span className="text-[10px]">Toolbar preview</span>
            <span role="img" aria-label="Node toolbar preview: activate or deactivate, delete, and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3">
              <Power className="size-3" />
              <Trash2 className="size-3" />
              <Split className="size-3 text-chart-2" />
            </span>
          </span>
        </p>
      </div>
      {connectorFactorName ? <div className="space-y-2 rounded-lg border px-3 py-2"><div className="flex items-center justify-between"><Label>Enabled</Label><Switch checked disabled /></div><p className="text-xs text-muted-foreground">Availability is controlled by connector factor {connectorFactorName}. Remove it to restore individual controls.</p>{Object.entries(bindings).map(([path, name]) => <div key={path} className="space-y-1"><p className="text-xs text-destructive">Individual factor {name} conflicts with this connector.</p><Button variant="outline" size="sm" onClick={() => unbindFactor(path)}>Remove individual binding</Button><p className="text-xs text-muted-foreground">Its declaration remains in Design until you remove or rebind it.</p></div>)}</div> : <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
        <div><Label htmlFor="script-enabled">Enabled</Label><p className="text-xs text-muted-foreground">Off: the wired agent never sees this script at all.</p>{bindings['config.enabled'] && <p className="text-xs text-chart-2">Factor: {bindings['config.enabled']}</p>}</div>
        <Switch id="script-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
      </div>}

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

      <div className="space-y-1.5">
        <Label htmlFor="script-description">Description and when to use it</Label>
        <Input
          id="script-description"
          value={config.description ?? ''}
          onChange={(e) => patchConfig({ description: e.target.value })}
          placeholder="What this script does and when the agent should run it"
        />
      </div>

      <div className="space-y-1.5"><Label>Code</Label>{bindings.config && <Button variant="ghost" size="sm" onClick={() => requestEditFactor(bindings.config)}>Factor: {bindings.config}</Button>}<PythonCodeEditor value={config.code} onChange={(code) => patchConfig({ code })} height="max(12rem, calc(100vh - 22rem))" /></div>

    </NodeInspectorDialog>
  )
}
