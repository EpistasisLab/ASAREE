import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedPersonas, personaId, PERSONA_FACTOR_PATH } from '@/lib/personaFactors'
import type { usePersonaTestSelection } from './usePersonaTestSelection'

export function PersonaTestSelectors({ selection }: { selection: ReturnType<typeof usePersonaTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedPersonas(selection.graph!, node.id)
    if (node.data.persona_factor_mode === 'persona_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[PERSONA_FACTOR_PATH] || node.data.label}: personas for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[PERSONA_FACTOR_PATH] || node.data.label}: persona for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => personaId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a persona…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={option.id}>{String(option.data.label)}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
