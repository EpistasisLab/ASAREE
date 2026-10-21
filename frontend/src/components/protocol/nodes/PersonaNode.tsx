import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { User } from 'lucide-react'
import type { PersonaNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'

// A persona node stores one instruction snapshot selected from the drawer.
const ACCENT = nodeAccent('persona')

export function PersonaNode({ id, data, selected }: NodeProps & { data: PersonaNodeData }) {
  const { updateNodeData } = useReactFlow()
  const { requestPersonaFactor, experimentLocked } = useProtocolCanvasActions()
  const controlled = !!data.personaFactorControlled
  const enabled = controlled || (data.config?.enabled ?? true)

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
      warning={data.config?.persona_id || data.config?.persona_text ? undefined : 'No persona selected'}
      factorCount={controlled ? 1 : boundFactorCount(data)}
      onMakeFactor={!experimentLocked && requestPersonaFactor ? () => requestPersonaFactor(id) : undefined}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={controlled || data.factor_bindings?.config ? undefined : () => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
