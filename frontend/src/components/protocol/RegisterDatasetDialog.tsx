import { useState, type DragEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { ApiError, datasetsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn, HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { Dataset } from '@/types/datasets'

// A drag-and-drop file field, same footprint as the plain Input it replaces
// (h-8, rounded-lg border) -- a <label> wrapping a visually-hidden (not
// display:none, so Tab still reaches it) file input, which is both the
// standard accessible custom-file-input pattern and gives this a natural
// drop target: the label itself listens for the drag events, no extra
// wrapper div needed.
function FileDropInput({
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
      <input
        id={id}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
    </label>
  )
}

// Registers a dataset directly from wherever a Dataset node needs one
// (DatasetNodeInspector) -- there's no dedicated Datasets page/route in this
// app (unlike Experiments' own tile grid), and nothing else references a
// dataset outside a protocol today, so a plain dialog at the one place this
// comes up is the right scope, not a new top-level resource.
//
// Only ONE csv is ever uploaded -- the split into train/test happens
// server-side (services.datasets.create_dataset's own group-aware
// GroupShuffleSplit / stratified train_test_split), so there's no "upload
// train, upload test" pair of fields here. test_size/seed are left at their
// server defaults (0.2/0) rather than exposed here -- not worth the extra
// form surface for a v1 upload flow.
//
// dictionary_json is included (unlike test_size/seed) because there's
// currently no PATCH /datasets/{id} -- once registered, this is the ONLY
// chance to set it; adding it after the fact means deleting and
// re-registering the whole dataset. Uploaded as its own JSON file, not
// pasted into a textarea -- matching the real workflow this mirrors (see
// asaree-spinal-use-case's own data/spine_data_dictionary.json, authored
// once by a data scientist and handed off as a file, not hand-typed at
// registration time). It's opaque to ASAREE itself (a domain MCP server
// like ares-sklearn-eda's get_data_dictionary is the one that actually
// reads it), so this only checks that the file parses as JSON at all, not
// any particular shape within it.
export function RegisterDatasetDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Fires with the freshly-created dataset so a caller (DatasetNodeInspector)
  // can immediately select it, the same way picking an existing one already does.
  onCreated?: (dataset: Dataset) => void
}) {
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [targetColumn, setTargetColumn] = useState('')
  const [groupColumn, setGroupColumn] = useState('')
  const [description, setDescription] = useState('')
  const [dictionaryFile, setDictionaryFile] = useState<File | null>(null)
  const queryClient = useQueryClient()

  function reset() {
    setName('')
    setFile(null)
    setTargetColumn('')
    setGroupColumn('')
    setDescription('')
    setDictionaryFile(null)
    createMutation.reset()
  }

  const createMutation = useMutation({
    mutationFn: async () => {
      let dictionaryJson: string | undefined
      if (dictionaryFile) {
        dictionaryJson = await dictionaryFile.text()
        try {
          JSON.parse(dictionaryJson)
        } catch {
          throw new Error(`"${dictionaryFile.name}" isn't valid JSON.`)
        }
      }
      return datasetsApi.create({
        name: name.trim(),
        file: file!,
        targetColumn: targetColumn.trim() || undefined,
        groupColumn: groupColumn.trim() || undefined,
        description: description.trim() || undefined,
        dictionaryJson,
      })
    },
    onSuccess: (dataset) => {
      queryClient.invalidateQueries({ queryKey: ['datasets'] })
      onCreated?.(dataset)
      reset()
      onOpenChange(false)
    },
  })

  const errorMessage = !createMutation.isError
    ? null
    : createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : createMutation.error instanceof Error
        ? createMutation.error.message
        : 'Could not register this dataset. Please try again.'

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className={HUD_ACCENT_RING_CLASSNAME}>
        <DialogHeader>
          <DialogTitle>Register a dataset</DialogTitle>
          <DialogDescription>
            Upload one CSV -- it's split into train/test server-side, so there's nothing to split by hand.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dataset-name">Name</Label>
              <Input id="dataset-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="spinal-fusion-v1" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dataset-file">CSV file</Label>
              <FileDropInput id="dataset-file" accept=".csv" file={file} onChange={setFile} placeholder="Drop a CSV or click to browse" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="dataset-target-column">Target column</Label>
              <Input id="dataset-target-column" value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)} placeholder="(optional)" />
              <p className="text-xs text-muted-foreground">Stratifies the split when there's no group column.</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="dataset-group-column">Group column</Label>
              <Input id="dataset-group-column" value={groupColumn} onChange={(e) => setGroupColumn(e.target.value)} placeholder="(optional)" />
              <p className="text-xs text-muted-foreground">Keeps each group's rows on one side of the split.</p>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dataset-description">Description</Label>
            <Textarea id="dataset-description" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="(optional)" />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="dataset-dictionary">Data dictionary</Label>
            <FileDropInput
              id="dataset-dictionary"
              accept=".json"
              file={dictionaryFile}
              onChange={setDictionaryFile}
              placeholder="Drop a JSON file or click to browse (optional)"
            />
            <p className="text-xs text-muted-foreground">
              Optional JSON file, opaque to ASAREE itself -- read by a domain MCP server's own EDA tools. There's no way
              to add or replace this after registering yet -- upload it now if you already have one.
            </p>
          </div>

          {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

          <DialogFooter>
            <Button onClick={() => createMutation.mutate()} disabled={!name.trim() || !file || createMutation.isPending}>
              {createMutation.isPending ? 'Registering…' : 'Register dataset'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  )
}
