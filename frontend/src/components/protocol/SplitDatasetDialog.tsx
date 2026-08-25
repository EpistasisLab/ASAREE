import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, datasetsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { Dataset } from '@/types/datasets'
import { FileDropInput } from './FileDropInput'

// Splitting a dataset's own raw file is a separate, later, optional action
// from registration (RegisterDatasetDialog) -- see RegisteredDataset's own
// comment in the backend model for why. Two ways to do it, as tabs rather
// than two separate dialogs (both act on the same dataset, so switching
// between them shouldn't lose your place): "Quick split" is ASAREE's own
// built-in group-aware/stratified holdout, a convenience for the common
// case; "Bring your own" registers an already-split train/test pair
// computed however the user needed (k-fold, time-based, a custom cohort
// rule, ...) -- the same "bring your own code" precedent the Script node
// already established for scoring. Re-splitting an already-split dataset is
// fine either way: both overwrite whichever split currently exists rather
// than accumulating one per call.
export function SplitDatasetDialog({
  open,
  onOpenChange,
  dataset,
  onSplit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  dataset: Dataset
  // Fires with the updated dataset (now carrying train/test paths) so a
  // caller (DatasetNodeInspector) can refresh its own view of it.
  onSplit?: (dataset: Dataset) => void
}) {
  // Prefilled from the split that currently exists, same as targetColumn
  // already was: a re-split usually changes ONE thing (a different seed, a
  // group column that was forgotten), and opening on 0.2/0 would silently
  // revert the rest. Falls back to the service's own defaults when there's
  // nothing to prefill from -- a never-split dataset, a manual split, or one
  // made before these were recorded.
  const [targetColumn, setTargetColumn] = useState(dataset.target_column ?? '')
  const [groupColumn, setGroupColumn] = useState(dataset.split_group_column ?? '')
  const [testSize, setTestSize] = useState(dataset.split_test_size ?? 0.2)
  const [seed, setSeed] = useState(dataset.split_seed ?? 0)
  const [trainFile, setTrainFile] = useState<File | null>(null)
  const [testFile, setTestFile] = useState<File | null>(null)
  const queryClient = useQueryClient()

  function reset() {
    setTargetColumn(dataset.target_column ?? '')
    setGroupColumn(dataset.split_group_column ?? '')
    setTestSize(dataset.split_test_size ?? 0.2)
    setSeed(dataset.split_seed ?? 0)
    setTrainFile(null)
    setTestFile(null)
    quickSplitMutation.reset()
    manualSplitMutation.reset()
  }

  function handleSuccess(updated: Dataset) {
    queryClient.invalidateQueries({ queryKey: ['datasets'] })
    onSplit?.(updated)
    reset()
    onOpenChange(false)
  }

  const quickSplitMutation = useMutation({
    mutationFn: () =>
      datasetsApi.quickSplit(dataset.id, {
        targetColumn: targetColumn.trim() || undefined,
        groupColumn: groupColumn.trim() || undefined,
        testSize,
        seed,
      }),
    onSuccess: handleSuccess,
  })

  const manualSplitMutation = useMutation({
    mutationFn: () => datasetsApi.manualSplit(dataset.id, { trainFile: trainFile!, testFile: testFile! }),
    onSuccess: handleSuccess,
  })

  function errorMessageFor(mutation: typeof quickSplitMutation | typeof manualSplitMutation): string | null {
    if (!mutation.isError) return null
    if (mutation.error instanceof ApiError && typeof mutation.error.detail === 'string') return mutation.error.detail
    if (mutation.error instanceof Error) return mutation.error.message
    return 'Could not split this dataset. Please try again.'
  }

  const alreadySplit = !!dataset.train_path

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
          <DialogTitle>Split "{dataset.name}"</DialogTitle>
          <DialogDescription>
            {alreadySplit
              ? "This dataset already has a split -- either option below replaces it."
              : 'Produces the train/test pair a workspace tool actually reads.'}
          </DialogDescription>
        </DialogHeader>

        <Tabs defaultValue="quick">
          <TabsList>
            <TabsTrigger value="quick">Quick split</TabsTrigger>
            <TabsTrigger value="manual">Bring your own</TabsTrigger>
          </TabsList>

          <TabsContent value="quick" className="space-y-4">
            <p className="text-xs text-muted-foreground">
              Group-aware if a group column is given and present in the data, else a stratified holdout on the target
              column.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="split-target-column">Target column</Label>
                <Input id="split-target-column" value={targetColumn} onChange={(e) => setTargetColumn(e.target.value)} placeholder="(optional)" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="split-group-column">Group column</Label>
                <Input id="split-group-column" value={groupColumn} onChange={(e) => setGroupColumn(e.target.value)} placeholder="(optional)" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="split-test-size">Test size</Label>
                <Input
                  id="split-test-size"
                  type="number"
                  min="0.01"
                  max="0.99"
                  step="0.05"
                  value={testSize}
                  onChange={(e) => setTestSize(Number(e.target.value))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="split-seed">Seed</Label>
                <Input id="split-seed" type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
              </div>
            </div>

            {errorMessageFor(quickSplitMutation) && <p className="text-sm text-destructive">{errorMessageFor(quickSplitMutation)}</p>}

            <DialogFooter>
              <Button onClick={() => quickSplitMutation.mutate()} disabled={quickSplitMutation.isPending}>
                {quickSplitMutation.isPending ? 'Splitting…' : 'Split dataset'}
              </Button>
            </DialogFooter>
          </TabsContent>

          <TabsContent value="manual" className="space-y-4">
            <p className="text-xs text-muted-foreground">
              Upload an already-split train/test pair -- ASAREE only checks that both parse as tabular data, not how
              they were split.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="split-train-file">Train CSV</Label>
                <FileDropInput id="split-train-file" accept=".csv" file={trainFile} onChange={setTrainFile} placeholder="Drop a CSV or click to browse" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="split-test-file">Test CSV</Label>
                <FileDropInput id="split-test-file" accept=".csv" file={testFile} onChange={setTestFile} placeholder="Drop a CSV or click to browse" />
              </div>
            </div>

            {errorMessageFor(manualSplitMutation) && <p className="text-sm text-destructive">{errorMessageFor(manualSplitMutation)}</p>}

            <DialogFooter>
              <Button onClick={() => manualSplitMutation.mutate()} disabled={!trainFile || !testFile || manualSplitMutation.isPending}>
                {manualSplitMutation.isPending ? 'Registering…' : 'Register split'}
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
