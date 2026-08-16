import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, datasetsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { Dataset } from '@/types/datasets'

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
// re-registering the whole dataset. It's opaque to ASAREE itself (a domain
// MCP server like ares-sklearn-eda's get_data_dictionary is the one that
// actually reads it), so this dialog doesn't validate its shape, just that
// it's present.
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
  const [dictionaryJson, setDictionaryJson] = useState('')
  const queryClient = useQueryClient()

  function reset() {
    setName('')
    setFile(null)
    setTargetColumn('')
    setGroupColumn('')
    setDescription('')
    setDictionaryJson('')
    createMutation.reset()
  }

  const createMutation = useMutation({
    mutationFn: () =>
      datasetsApi.create({
        name: name.trim(),
        file: file!,
        targetColumn: targetColumn.trim() || undefined,
        groupColumn: groupColumn.trim() || undefined,
        description: description.trim() || undefined,
        dictionaryJson: dictionaryJson.trim() || undefined,
      }),
    onSuccess: (dataset) => {
      queryClient.invalidateQueries({ queryKey: ['datasets'] })
      onCreated?.(dataset)
      reset()
      onOpenChange(false)
    },
  })

  const errorMessage =
    createMutation.error instanceof ApiError && typeof createMutation.error.detail === 'string'
      ? createMutation.error.detail
      : createMutation.isError
        ? 'Could not register this dataset. Please try again.'
        : null

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
              <Input id="dataset-file" type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
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
            <Textarea
              id="dataset-dictionary"
              rows={6}
              className="font-mono text-xs"
              value={dictionaryJson}
              onChange={(e) => setDictionaryJson(e.target.value)}
              placeholder="(optional) JSON, opaque to ASAREE -- read by a domain MCP server's own EDA tools"
            />
            <p className="text-xs text-muted-foreground">
              There's no way to edit this after registering yet -- add it now if you already have one.
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
