import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { FileText } from 'lucide-react'
import type { OkfDocumentNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Names one uploaded OKF concept document -- the Knowledge connector's other
// node type, identical to OkfBundleNode at run time (its own MCP server's
// tools join the agent's allow-list via _resolve_knowledge_config) and
// therefore solid rather than dashed for the same reason.
//
// Its own hue rather than sharing the bundle's: they sit side by side on the
// same connector, and "one file I uploaded" vs. "a folder on the server" is
// exactly the distinction a glance at the canvas should make.
const ACCENT = nodeAccent('okf_document')

export function OkfDocumentNode({ id, data, selected }: NodeProps & { data: OkfDocumentNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true
  // server_name, not document_id, is what a run reads -- same as the bundle
  // node, and for the same reason.
  const warning = !data.config?.server_name
    ? 'No document selected'
    : (data.config?.tool_names?.length ?? 0) === 0
      ? 'No tools discovered -- the document server may have failed to start'
      : undefined

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={FileText}
      label={data.label}
      placeholder="OKF Document"
      handleId="knowledge"
      handlePosition="bottom"
      warning={warning}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
