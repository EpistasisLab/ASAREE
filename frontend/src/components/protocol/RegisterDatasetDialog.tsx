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
import { FileDropInput } from './FileDropInput'

// Registers a dataset from wherever one is needed -- the dataset browser
// (DatasetBrowserPanel, where the library is listed) and a Dataset node's own
// inspector. There's no dedicated Datasets page/route in this app (unlike
// Experiments' own tile grid), and nothing references a dataset outside a
// protocol today, so a dialog at the two places this comes up is the right
// scope, not a new top-level section.
//
// This ONLY stores the raw uploaded file -- it never splits it (see
// RegisteredDataset's own comment in the backend model for why: scientific
// splitting needs vary per experiment, and baking exactly one strategy into
// registration, irreversibly discarding the source, made every other
// strategy unreachable without re-uploading from scratch). Splitting is a
// separate action (SplitDatasetDialog) against the raw file this dialog
// registers, available once the dataset exists.
//
// dictionary_json is included at registration (unlike split params, which
// don't belong here at all) because there's currently no PATCH
// /datasets/{id} -- once registered, this is the ONLY chance to set it.
// Uploaded as its own JSON file, not pasted into a textarea -- matching the
// real workflow this mirrors (see asaree-spinal-use-case's own
// data/spine_data_dictionary.json, authored once by a data scientist and
// handed off as a file, not hand-typed at registration time). It's opaque
// to ASAREE itself (a domain MCP server like ares-sklearn-eda's
// get_data_dictionary is the one that actually reads it), so this only
// checks that the file parses as JSON at all, not any particular shape
// within it.
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
  const [description, setDescription] = useState('')
  const [dictionaryFile, setDictionaryFile] = useState<File | null>(null)
  const queryClient = useQueryClient()

  function reset() {
    setName('')
    setFile(null)
    setTargetColumn('')
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
            Uploads the raw file as-is -- it's never split here. Split it (however you need to) once it's registered.
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

          <div className="space-y-1.5">
            <Label htmlFor="dataset-target-column">Target column</Label>
            <Input id="dataset-target-column" value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)} placeholder="(optional)" />
            <p className="text-xs text-muted-foreground">Descriptive for now -- also used as the default stratification column if you later use the quick split.</p>
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
