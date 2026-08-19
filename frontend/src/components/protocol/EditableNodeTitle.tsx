import { useState } from 'react'
import { Input } from '@/components/ui/input'

// A node's rename mechanism: click the title in its Inspector header to edit
// it in place -- same click-to-rename pattern
// ProtocolCanvasPage.tsx's EditableExperimentName already uses for the
// experiment name, reused here instead of a second bespoke mechanism.
// Replaces the earlier double-click-the-label-on-the-canvas-card approach
// (removed entirely, not kept alongside this) -- a canvas card is small and
// mostly taken up by other content, so renaming from the Inspector's own
// title, which is always full-width and unambiguous, is the more
// discoverable of the two.
export function EditableNodeTitle({
  label,
  placeholder,
  onCommit,
}: {
  label: string
  placeholder: string
  onCommit: (label: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(label)

  function commit() {
    setEditing(false)
    const trimmed = value.trim()
    if (trimmed && trimmed !== label) onCommit(trimmed)
    else setValue(label)
  }

  if (editing) {
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
            setEditing(false)
          }
        }}
        className="h-8 w-56 text-lg font-semibold"
      />
    )
  }

  return (
    <button
      type="button"
      onClick={() => {
        setValue(label)
        setEditing(true)
      }}
      title="Click to rename"
      className="-ml-1.5 cursor-pointer rounded-md px-1.5 py-0.5 text-lg font-semibold hover:bg-muted"
    >
      {label || placeholder}
    </button>
  )
}
