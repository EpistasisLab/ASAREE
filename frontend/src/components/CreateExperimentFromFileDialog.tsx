import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Upload } from 'lucide-react'
import { experimentsApi, packageImportsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { newRequestKey } from '@/lib/requestKey'
import { FileDropInput } from '@/components/protocol/FileDropInput'
import type { DesignSpec, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

type JsonRecord = Record<string, unknown>

interface ImportedDefinition {
  sourceName: string
  description: string | null
  hypothesis: string | null
  designType: string
  taskBrief: Record<string, unknown> | null
  designSpec: DesignSpec | null
  measurementPlan: MeasurementPlan | null
  graph: ProtocolGraph
  protocolDescription: string | null
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function optionalString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function parseDefinition(text: string): ImportedDefinition {
  const parsed: unknown = JSON.parse(text)
  if (!isRecord(parsed) || !isRecord(parsed.graph) || !Array.isArray(parsed.graph.nodes) || !Array.isArray(parsed.graph.edges)) {
    throw new Error('This file is not a valid ASAREE experiment definition.')
  }

  // The full portable export nests experiment metadata under `experiment`.
  // Reading the old top-level fields too keeps pre-portable canvas downloads
  // usable, while still creating a new experiment rather than merging them.
  const experiment = isRecord(parsed.experiment) ? parsed.experiment : parsed
  const canvas = isRecord(parsed.canvas) ? parsed.canvas : parsed
  const sourceName = optionalString(experiment.name)?.trim() || optionalString(parsed.name)?.trim() || 'Imported Experiment'

  return {
    sourceName,
    description: optionalString(experiment.description) ?? optionalString(parsed.description),
    hypothesis: optionalString(experiment.hypothesis),
    designType: optionalString(experiment.design_type) ?? 'factorial',
    taskBrief: isRecord(experiment.task_brief) ? experiment.task_brief : null,
    designSpec: (isRecord(experiment.design_spec) ? experiment.design_spec : isRecord(parsed.design_spec) ? parsed.design_spec : null) as DesignSpec | null,
    measurementPlan: (isRecord(experiment.measurement_plan)
      ? experiment.measurement_plan
      : isRecord(parsed.measurement_plan)
        ? parsed.measurement_plan
        : null) as unknown as MeasurementPlan | null,
    graph: parsed.graph as unknown as ProtocolGraph,
    protocolDescription: optionalString(canvas.description) ?? optionalString(parsed.description),
  }
}

export function CreateExperimentFromFileDialog({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onImported: (experimentId: string) => void
}) {
  const [definition, setDefinition] = useState<ImportedDefinition | null>(null)
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const [packageId, setPackageId] = useState<string>()
  const isPackage = file?.name.toLowerCase().endsWith('.tar.gz') ?? false
  const packageJob = useQuery({ queryKey: ['package-import', packageId], enabled: !!packageId,
    queryFn: () => packageImportsApi.get(packageId!),
    refetchInterval: query => ['completed', 'failed', 'cancelled', 'expired'].includes(query.state.data?.status ?? '') ? false : 1000 })
  useEffect(() => {
    if (packageJob.data?.status !== 'completed' || !packageJob.data.experiment_id) return
    void queryClient.invalidateQueries({ queryKey: ['experiments'] })
    setPackageId(undefined)
    onOpenChange(false)
    onImported(packageJob.data.experiment_id)
  }, [packageJob.data, queryClient, onOpenChange, onImported])

  const importMutation = useMutation({
    mutationFn: async () => {
      if (isPackage && file) {
        const job = await packageImportsApi.create(newRequestKey())
        await packageImportsApi.upload(job.id, file)
        setPackageId(job.id)
        return null
      }
      if (!definition) throw new Error('Choose an experiment definition first.')
      return experimentsApi.importDefinition({
        name: name.trim(),
        description: definition.description,
        hypothesis: definition.hypothesis,
        design_type: definition.designType,
        task_brief: definition.taskBrief,
        design_spec: definition.designSpec,
        measurement_plan: definition.measurementPlan,
        graph: definition.graph,
        protocol_description: definition.protocolDescription,
      })
    },
    onSuccess: (experiment) => {
      if (!experiment) return
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      onOpenChange(false)
      onImported(experiment.id)
    },

  })

  function reset() {
    importMutation.reset()
    setDefinition(null)
    setName('')
    setFile(null)
    setFileError(null)
  }

  function handleOpenChange(next: boolean) {
    if (next && !packageId) reset()
    onOpenChange(next)
  }

  async function handleFileSelected(file: File | null) {
    if (!file) return
    setPackageId(undefined)
    importMutation.reset()
    if (file.name.toLowerCase().endsWith('.tar.gz')) {
      setDefinition(null)
      setFile(file)
      setFileError(null)
      return
    }
    setFileError(null)
    try {
      const parsed = parseDefinition(await file.text())
      setDefinition(parsed)
      setFile(file)
      setName(parsed.sourceName)
    } catch (error) {
      setDefinition(null)
      setFile(null)
      setName('')
      setFileError(error instanceof Error ? error.message : 'Could not read this file as JSON.')
    }
  }

  const packageActive = !!packageId && !['completed', 'failed', 'cancelled', 'expired'].includes(packageJob.data?.status ?? '')
  const busy = importMutation.isPending || packageActive
  const mutationError = importMutation.error instanceof Error ? importMutation.error.message : null

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create experiment from file</DialogTitle>
          <DialogDescription>
            Create an unpublished editable draft from JSON, or restore the experiment and its input assets from a package. Results are excluded.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="experiment-definition-file">Experiment definition</Label>
            <FileDropInput
              id="experiment-definition-file"
              accept="application/json,.json,application/gzip,.gz,.tar.gz"
              disabled={busy}
              file={file}
              onChange={(next) => void handleFileSelected(next)}
              placeholder="Drop a JSON or .tar.gz file or click to browse"
            />
          </div>
          {definition && (
            <div className="space-y-1.5">
              <Label htmlFor="import-experiment-name">New experiment name</Label>
              <Input
                id="import-experiment-name"
                value={name}
                onChange={(event) => {
                  setName(event.target.value)
                  importMutation.reset()
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && name.trim()) importMutation.mutate()
                }}
              />

            </div>
          )}
          {packageId && <p role="status" className="text-sm">{packageJob.data?.status === 'failed'
            ? packageJob.data.error : `Creating experiment… ${packageJob.data?.progress.phase ?? 'Uploading'}`}</p>}
          {packageJob.error && <p role="alert">{packageJob.error.message}</p>}
          {packageJob.data?.status === 'failed' && <Button variant="outline" onClick={() => {
            void packageImportsApi.command(packageId!, 'retry').then(() => packageJob.refetch())
          }}>Retry import</Button>}
          {fileError && <p className="text-sm text-destructive">{fileError}</p>}
          {importMutation.isError && <p className="text-sm text-destructive">{mutationError ?? 'Could not import this experiment.'}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => {
            if (packageId) void packageImportsApi.command(packageId, 'cancel')
            setPackageId(undefined)
            onOpenChange(false)
          }} disabled={importMutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => importMutation.mutate()} disabled={(!isPackage && (!definition || !name.trim())) || busy || !file}>
            <Upload className="size-4" />
            {busy ? 'Creating…' : 'Create experiment'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
