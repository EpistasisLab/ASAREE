import { useState, type DragEvent } from 'react'
import { Upload } from 'lucide-react'
import { cn } from '@/lib/utils'

// A drag-and-drop file field, same footprint as a plain Input (h-8,
// rounded-lg border) -- a <label> wrapping a visually-hidden (not
// display:none, so Tab still reaches it) file input, which is both the
// standard accessible custom-file-input pattern and gives this a natural
// drop target: the label itself listens for the drag events, no extra
// wrapper div needed. Shared by RegisterDatasetDialog (raw CSV, data
// dictionary) and SplitDatasetDialog (train/test CSVs for a manual split).
export function FileDropInput({
  id,
  accept,
  file,
  onChange,
  placeholder,
}: {
  id: string
  accept: string
  file: File | null
  onChange: (file: File | null) => void
  placeholder: string
}) {
  const [dragOver, setDragOver] = useState(false)

  function handleDrop(e: DragEvent<HTMLLabelElement>) {
    e.preventDefault()
    setDragOver(false)
    const dropped = e.dataTransfer.files?.[0]
    if (dropped) onChange(dropped)
  }

  return (
    <label
      htmlFor={id}
      onDragOver={(e) => {
        e.preventDefault()
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        'flex h-8 w-full cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-input px-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted/50',
        dragOver && 'border-ring bg-muted/50 ring-3 ring-ring/50',
      )}
    >
      <Upload className="size-3.5 shrink-0" />
      <span className={cn('truncate', file && 'font-mono text-xs text-foreground')}>{file ? file.name : placeholder}</span>
      <input id={id} type="file" accept={accept} className="sr-only" onChange={(e) => onChange(e.target.files?.[0] ?? null)} />
    </label>
  )
}
