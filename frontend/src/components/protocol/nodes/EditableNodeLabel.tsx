import { useState } from 'react'
import { useReactFlow } from '@xyflow/react'
import { Input } from '@/components/ui/input'

// n8n's own rename mechanism is "double-click opens the NDV modal, edit the
// title there" -- ASAREE already has a better precedent for exactly this
// (ProtocolCanvasPage.tsx's EditableExperimentName click-to-edit-inline),
// reused here instead of building a second modal-based mechanism. `renaming`
// is lifted to the parent node card (not owned here) so both a direct
// double-click on the label AND the hover toolbar's "..." > Rename item can
// trigger the same edit mode.
export function EditableNodeLabel({
  nodeId,
  label,
  placeholder,
  renaming,
  onRenamingChange,
}: {
  nodeId: string
  label: string
  placeholder: string
  renaming: boolean
  onRenamingChange: (renaming: boolean) => void
}) {
  const { updateNodeData } = useReactFlow()
  const [value, setValue] = useState(label)

  function commit() {
    onRenamingChange(false)
    updateNodeData(nodeId, { label: value }) // merges by default -- doesn't clobber config/factor_bindings
  }

  if (renaming) {
    return (
      <Input
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') {
            setValue(label)
            onRenamingChange(false)
          }
        }}
        onClick={(e) => e.stopPropagation()}
        className="h-5 min-w-0 flex-1 px-1 py-0 text-xs"
      />
    )
  }

  return (
    <span
      // flex-1 (the parent row is a flex container in every node card)
      // stretches this span's own box across the row's remaining width,
      // not just as wide as its own text -- otherwise the actual
      // double-click target is only the tight text itself, and a wider
      // card (e.g. AgentNode, which has much more empty row space beside
      // a short label) makes it easy to double-click just next to the
      // text and land on the card's own onDoubleClick (opens the
      // Inspector) instead of renaming.
      className="min-w-0 flex-1 truncate text-xs font-medium"
      title={label}
      onDoubleClick={(e) => {
        // Overrides the card's own onNodeDoubleClick (opens the inspector)
        // for this element specifically -- the rest of the card still opens
        // the inspector as before.
        e.stopPropagation()
        setValue(label)
        onRenamingChange(true)
      }}
    >
      {label || placeholder}
    </span>
  )
}
