import type { NodeProps } from '@xyflow/react'
import { Database } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { DatasetNodeData } from '@/types/protocols'
import { CircleNode } from './CircleNode'

// Declares which registered dataset an Agent's workspace tools operate on --
// a real runtime effect once wired (see DatasetNodeData's own comment in
// types/protocols.ts), unlike Memory/Pattern's own "declares intent, no
// effect yet" status, so NOT dashed. Rendered as n8n's own small
// circle-with-icon (see CircleNode), same as every other connector source.
const ACCENT = hashToChartHue('dataset')

export function DatasetNode({ id, data, selected }: NodeProps & { data: DatasetNodeData }) {
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Database}
      label={data.label}
      placeholder="Dataset"
      handleId="dataset"
      warning={data.config?.dataset_id ? undefined : 'No dataset selected'}
    />
  )
}
