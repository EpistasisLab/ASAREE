import { useId, useState } from 'react'
import { nodeAccent } from '@/lib/nodeAccent'
import { User, Upload, Library, Plus, X } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PERSONA_LIBRARY } from './personaLibrary'
import type { ProtocolNode, PersonaNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('persona')


export function PersonaNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: PersonaNodeData }) | null
  experimentId: string | null
  factorNodeLabel: string
  onChange: (nodeId: string, data: PersonaNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const uploadId = useId()
  // Track multiple selected personas for combination (can include empty strings for unfilled slots)
  const [selectedPersonas, setSelectedPersonas] = useState<string[]>(() => {
    if (!node) return []
    const config = node.data.config
    // Initialize from persona_ids array if present, otherwise fall back to single persona_id
    if (config.persona_ids && Array.isArray(config.persona_ids) && config.persona_ids.length > 0) {
      return config.persona_ids as string[]
    }
    return config.persona_id ? [config.persona_id] : []
  })

  if (!node) return null
  const nodeId = node.id
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<PersonaNodeData['config']>) {
    onChange(nodeId, { ...data, config: { ...config, ...patch } })
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(nodeId, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(nodeId, { ...data, factor_bindings: next })
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    const text = await file.text()
    patchConfig({
      persona_text: text,
      persona_file: file.name,
      mode: 'file'
    })
  }

  return (
    <NodeInspectorDialog
      open={true}
      onOpenChange={onClose}
      accent={ACCENT}
      title={
        <>
          <User className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Persona" onCommit={(label) => onChange(nodeId, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(nodeId)}
      onClose={onClose}
    >
      <div className="space-y-6">

        {/* Choose from library - multi-select to combine personas */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>Choose from library</Label>
            {selectedPersonas.length > 0 && selectedPersonas.length < 5 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedPersonas([...selectedPersonas, ''])}
                className="h-7 text-xs"
              >
                <Plus className="size-3 mr-1" />
                Add another
              </Button>
            )}
          </div>

          {selectedPersonas.length === 0 ? (
            <Select
              value=""
              onValueChange={(personaId) => {
                const persona = PERSONA_LIBRARY.find(p => p.id === personaId)
                if (persona && personaId) {
                  setSelectedPersonas([personaId])
                  patchConfig({
                    persona_text: persona.prompt || '',
                    persona_id: persona.id || null,
                    persona_label: persona.label || null,
                    mode: 'library'
                  })
                }
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a validated persona..." />
              </SelectTrigger>
              <SelectContent>
                {PERSONA_LIBRARY.map(p => (
                  <SelectItem key={p.id} value={p.id}>
                    <div className="flex items-center gap-2">
                      <Library className="size-4" />
                      {p.label}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <div className="space-y-2">
              {selectedPersonas.map((personaId, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Select
                    value={personaId || ''}
                    onValueChange={(newId) => {
                      const newSelected = [...selectedPersonas]
                      newSelected[index] = newId || ''
                      const filtered = newSelected.filter(id => id)
                      setSelectedPersonas(newSelected)

                      // Combine all selected persona texts
                      const combinedText = filtered
                        .map(id => PERSONA_LIBRARY.find(p => p.id === id)?.prompt ?? '')
                        .filter(text => text)
                        .join('\n\n')

                      const labels = filtered
                        .map(id => PERSONA_LIBRARY.find(p => p.id === id)?.label)
                        .filter(Boolean)
                        .join(' + ')

                      patchConfig({
                        persona_text: combinedText,
                        persona_id: filtered[0] || null,
                        persona_ids: filtered, // Store all IDs for multi-select restoration
                        persona_label: labels || null,
                        mode: 'library'
                      })
                    }}
                  >
                    <SelectTrigger className="flex-1">
                      <SelectValue placeholder={`Select persona ${index + 1}...`} />
                    </SelectTrigger>
                    <SelectContent>
                      {PERSONA_LIBRARY.map(p => (
                        <SelectItem key={p.id} value={p.id}>
                          <div className="flex items-center gap-2">
                            <Library className="size-4" />
                            {p.label}
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    onClick={() => {
                      const newSelected = selectedPersonas.filter((_, i) => i !== index)
                      setSelectedPersonas(newSelected)

                      if (newSelected.length === 0) {
                        patchConfig({
                          persona_text: '',
                          persona_id: null,
                          persona_ids: [],
                          persona_label: null,
                          mode: 'text'
                        })
                      } else {
                        // Recombine remaining personas
                        const filtered = newSelected.filter(id => id)
                        const combinedText = filtered
                          .map(id => PERSONA_LIBRARY.find(p => p.id === id)?.prompt ?? '')
                          .filter(text => text)
                          .join('\n\n')

                        const labels = filtered
                          .map(id => PERSONA_LIBRARY.find(p => p.id === id)?.label)
                          .filter(Boolean)
                          .join(' + ')

                        patchConfig({
                          persona_text: combinedText,
                          persona_id: filtered[0] || null,
                          persona_ids: filtered, // Store all IDs for multi-select restoration
                          persona_label: labels || null,
                          mode: 'library'
                        })
                      }
                    }}
                  >
                    <X className="size-3" />
                  </Button>
                </div>
              ))}
              {selectedPersonas.length > 1 && (
                <p className="text-xs text-muted-foreground">
                  Combining {selectedPersonas.filter(id => id).length} personas
                </p>
              )}
            </div>
          )}
        </div>

        {/* Persona Markdown upload */}
        <div className="space-y-2">
          <Label htmlFor={uploadId}>Upload persona (.md)</Label>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => document.getElementById(uploadId)?.click()}
            >
              <Upload className="size-4 mr-2" />
              {config.persona_file || 'Choose .md file'}
            </Button>
            <input
              id={uploadId}
              type="file"
              accept=".md"
              className="hidden"
              onChange={handleFileUpload}
            />
          </div>
          <p className="text-xs text-muted-foreground">Upload any .md file containing personality or behavior instructions to prepend to the agent's system prompt.</p>
        </div>

        {/* Persona text with factor binding - toolbar creation like AgentNodeInspector */}
        <FactorBindableField
          experimentId={experimentId}
          nodeId={node.id}
          fieldPath="config"
          creationInToolbar
          defaultLabel="Persona"
          nodeLabel={factorNodeLabel}
          levelType="persona_text"
          currentValue={config}
          boundFactorName={bindings.config}
          onBind={(name) => bindFactor('config', name)}
          onUnbind={() => unbindFactor('config')}
        >
          {(trigger) => trigger}
        </FactorBindableField>

        {/* Persona text editor - all functionality preserved */}
        <div className="space-y-2">
          <Label htmlFor="persona-text">Persona text</Label>
          <Textarea
            id="persona-text"
            value={config.persona_text || ''}
            onChange={(e) => patchConfig({ persona_text: e.target.value })}
            placeholder="You are extremely organized, very responsible, and very hardworking..."
            className="min-h-32 font-mono text-sm"
          />
        </div>

        {/* Help text */}
        <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground space-y-1">
          <p className="font-medium">How personas work:</p>
          <p>Persona text is <strong>added to the beginning of the agent's system prompt</strong> at runtime.</p>
          <p className="font-medium mt-2">Library personas:</p>
          <p>Pre-defined personality configurations based on <strong>OCEAN personality dimensions</strong> (Openness, Conscientiousness, Extraversion, Agreeableness, Neuroticism). Includes combined profiles (Analyst, Explorer, Critic) and individual trait isolations (High/Low O, C, E, A, N). Use "Add another" to combine up to 5 personas.</p>
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
