import { nodeAccent } from '@/lib/nodeAccent'
import { User, Upload, Library } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { ProtocolNode, PersonaNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('persona')

// Validated persona library from persona_library_v1.json
const PERSONA_LIBRARY = [
  {
    id: 'none_v1',
    label: 'None (baseline)',
    prompt: ''
  },
  {
    id: 'analyst_v1',
    label: 'Analyst',
    prompt: 'You are extremely organized, very responsible, and very hardworking. You are extremely orderly and very thorough. You are a bit uncreative and a bit predictable. You are a bit curious. You are moderately relaxed and emotionally stable. You are moderately cooperative.'
  },
  {
    id: 'explorer_v1',
    label: 'Explorer',
    prompt: 'You are extremely curious and very spontaneous. You are very creative and very imaginative. You are extremely energetic. You are very adventurous and daring. You are very talkative and very extraverted. You are moderately organized. You are moderately agreeable and cooperative. You are very relaxed and emotionally stable.'
  },
  {
    id: 'critic_v1',
    label: 'Critic',
    prompt: 'You are extremely organized, very thorough, and very self-disciplined. You are a bit unsympathetic and a bit distrustful. You are a bit cooperative. You are moderately curious. You are a bit anxious and a bit tense.'
  },
  {
    id: 'agreeable_evaluator_v1',
    label: 'Pushover',
    prompt: 'You are extremely altruistic and very cooperative. You are very lazy and extremely irresponsible.'
  }
]

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
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<PersonaNodeData['config']>) {
    onChange(node.id, { ...data, config: { ...config, ...patch } })
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(node.id, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(node.id, { ...data, factor_bindings: next })
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
          <EditableNodeTitle label={data.label} placeholder="Persona" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
      factorNodeLabel={factorNodeLabel}
    >
      <div className="space-y-6">
        {/* Enable/disable switch */}
        <div className="flex items-center justify-between">
          <Label htmlFor="persona-enabled">Enabled</Label>
          <Switch
            id="persona-enabled"
            checked={config.enabled ?? true}
            onCheckedChange={(enabled) => patchConfig({ enabled })}
          />
        </div>

        {/* Choose from library */}
        <div className="space-y-2">
          <Label>Choose from library</Label>
          <Select
            value=""
            onValueChange={(personaId) => {
              const persona = PERSONA_LIBRARY.find(p => p.id === personaId)
              if (persona) {
                patchConfig({
                  persona_text: persona.prompt,
                  persona_id: persona.id,
                  persona_label: persona.label,
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
        </div>

        {/* File upload for skill.md */}
        <div className="space-y-2">
          <Label>Upload skill.md file</Label>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => document.getElementById('persona-file-upload')?.click()}
            >
              <Upload className="size-4 mr-2" />
              {config.persona_file || 'Choose .md file'}
            </Button>
            <input
              id="persona-file-upload"
              type="file"
              accept=".md"
              className="hidden"
              onChange={handleFileUpload}
            />
          </div>
        </div>

        {/* Persona text with factor binding - matches AgentNodeInspector pattern */}
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.persona_text"
          defaultLabel="Persona text"
          nodeLabel={data.label || 'Persona'}
          levelType="persona_text"
          currentValue={config.persona_text}
          boundFactorName={bindings['config.persona_text']}
          onBind={(name) => bindFactor('config.persona_text', name)}
          onUnbind={() => unbindFactor('config.persona_text')}
        >
          {(trigger) => (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="persona-text">Persona text</Label>
                {trigger}
              </div>
              <Textarea
                id="persona-text"
                value={config.persona_text || ''}
                onChange={(e) => patchConfig({ persona_text: e.target.value })}
                placeholder="You are extremely organized, very responsible, and very hardworking..."
                className="min-h-32 font-mono text-sm"
              />
            </div>
          )}
        </FactorBindableField>

        {/* Help text */}
        <div className="rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
          <p className="font-medium mb-1">How personas work:</p>
          <p>Persona text is <strong>added to the beginning of the agent's system prompt</strong> at runtime.</p>
        </div>
      </div>
    </NodeInspectorDialog>
  )
}
