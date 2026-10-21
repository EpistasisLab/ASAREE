import { useId } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Upload } from 'lucide-react'
import { personasApi } from '@/api/client'
import { nodeAccent } from '@/lib/nodeAccent'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PersonaLibraryPicker } from './PersonaLibraryPicker'
import { configForSavedPersona } from './usePersonaLibrary'
import type { ProtocolNode, PersonaNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('persona')

export function PersonaNodeInspector({ node, experimentId, factorNodeLabel, onChange, onDelete, onClose }: {
  node: (ProtocolNode & { data: PersonaNodeData }) | null
  experimentId: string | null
  factorNodeLabel: string
  onChange: (nodeId: string, data: PersonaNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const uploadId = useId()
  const client = useQueryClient()
  const upload = useMutation({
    mutationFn: personasApi.upload,
    onSuccess: () => client.invalidateQueries({ queryKey: ['personas'] }),
  })
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  function patchConfig(patch: Partial<PersonaNodeData['config']>) {
    if (node) onChange(node.id, { ...data, config: { ...config, ...patch } })
  }
  function bindFactor(name: string | null) {
    if (!node) return
    const next = { ...bindings }
    if (name) next.config = name
    else delete next.config
    onChange(node.id, { ...data, factor_bindings: next })
  }
  return (
    <NodeInspectorDialog open onOpenChange={onClose} accent={ACCENT}
      title={<><User className="size-5" style={{ color: ACCENT }} /><EditableNodeTitle label={data.label} placeholder="Persona" onCommit={label => onChange(node.id, { ...data, label })} /></>}
      onDelete={() => onDelete(node.id)} onClose={onClose}>
      <div className="space-y-6">
        <PersonaLibraryPicker value={config} onChange={patchConfig} />
        <div className="space-y-2">
          <Label htmlFor={uploadId}>Upload persona (.md)</Label>
          <Button variant="outline" size="sm" className="w-full" disabled={upload.isPending} onClick={() => document.getElementById(uploadId)?.click()}>
            <Upload className="mr-2 size-4" />{upload.isPending ? 'Saving persona…' : config.persona_file || 'Choose .md file'}
          </Button>
          <input id={uploadId} type="file" accept=".md" className="hidden" onChange={event => {
            const file = event.target.files?.[0]
            if (file) upload.mutate(file, { onSuccess: persona => patchConfig({ ...configForSavedPersona(persona), mode: 'file' }) })
            event.target.value = ''
          }} />
          <p className="text-xs text-muted-foreground">Upload plain Markdown instructions. No frontmatter is required. A reusable persona is saved with a name suggested from the filename, and its text is copied here.</p>
          {upload.error && <p role="alert" className="text-sm text-destructive">{upload.error.message}</p>}
        </div>
        <FactorBindableField experimentId={experimentId} nodeId={node.id} fieldPath="config" creationInToolbar defaultLabel="Persona" nodeLabel={factorNodeLabel}
          levelType="persona_text" currentValue={config} boundFactorName={bindings.config} onBind={bindFactor} onUnbind={() => bindFactor(null)}>
          {trigger => trigger}
        </FactorBindableField>
        <div className="space-y-2">
          <Label htmlFor={`${uploadId}-text`}>Persona text</Label>
          <Textarea id={`${uploadId}-text`} value={config.persona_text || ''} onChange={event => patchConfig({
            persona_text: event.target.value, mode: 'text', persona_id: null, persona_ids: [], persona_sources: [], persona_label: null, persona_file: null,
          })} placeholder="Describe the agent's personality and behavior…" className="min-h-32 font-mono text-sm" />
        </div>
        <p className="text-xs text-muted-foreground">Persona text is prepended to the agent's system prompt. Library edits do not change this snapshot. Publish the experiment to use changes in production runs.</p>
      </div>
    </NodeInspectorDialog>
  )
}
