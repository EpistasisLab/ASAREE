import { nodeAccent } from '@/lib/nodeAccent'
import { type NodeProps } from '@xyflow/react'
import { User } from 'lucide-react'
import type { PersonaNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// Configures an agent's personality traits (OCEAN framework) for experimental
// manipulation. Three modes: Library (validated personas), Text (direct input),
// or File (.md upload). Persona text prepends to the agent's system_prompt.
// Real runtime effect via _resolve_persona_configs in protocol_execution.py.
// Wires into Agent's persona connector (handleId="persona"), matching
// CONNECTOR_PANEL_INFO.persona in ProtocolCanvas.tsx and _NODE_TYPE_TO_HANDLE
// in protocol_execution.py. Connects via top handle like Dataset/Skill.
const ACCENT = nodeAccent('persona')

export function PersonaNode({ id, data, selected }: NodeProps & { data: PersonaNodeData }) {
  const { requestPersonaFactor, experimentLocked } = useProtocolCanvasActions()

  // Determine warning based on mode
  let warning: string | undefined
  const mode = data.config?.mode || 'library'

  if (mode === 'library' && !data.config?.persona_id) {
    warning = 'No persona selected'
  } else if (mode === 'text' && !data.config?.persona_text) {
    warning = 'No persona text provided'
  } else if (mode === 'file' && !data.config?.persona_file) {
    warning = 'No persona file uploaded'
  }

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={User}
      label={data.label}
      placeholder="Persona"
      handleId="persona"
      handlePosition="bottom"
      warning={warning}
      factorCount={boundFactorCount(data)}
      onMakeFactor={!experimentLocked && requestPersonaFactor ? () => requestPersonaFactor(id) : undefined}
    />
  )
}
