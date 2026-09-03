import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileJson, Upload } from 'lucide-react'
import { ApiError, experimentsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { DesignSpec } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

type JsonRecord = Record<string, unknown>

interface ImportedDefinition {
  sourceName: string
  description: string | null
  hypothesis: string | null
  designType: string
  taskBrief: Record<string, unknown> | null
  designSpec: DesignSpec | null
  graph: ProtocolGraph
  publishedGraph: ProtocolGraph | null
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
  const published = isRecord(canvas.published) ? canvas.published : null
  const publishedGraph = published && isRecord(published.graph) && Array.isArray(published.graph.nodes) && Array.isArray(published.graph.edges)
    ? published.graph as unknown as ProtocolGraph
    : null
  const sourceName = optionalString(experiment.name)?.trim() || optionalString(parsed.name)?.trim() || 'Imported Experiment'

  return {
    sourceName,
    description: optionalString(experiment.description) ?? optionalString(parsed.description),
    hypothesis: optionalString(experiment.hypothesis),
    designType: optionalString(experiment.design_type) ?? 'factorial',
    taskBrief: isRecord(experiment.task_brief) ? experiment.task_brief : null,
    designSpec: (isRecord(experiment.design_spec) ? experiment.design_spec : isRecord(parsed.design_spec) ? parsed.design_spec : null) as DesignSpec | null,
    graph: parsed.graph as unknown as ProtocolGraph,
    publishedGraph,
    protocolDescription: optionalString(canvas.description) ?? optionalString(parsed.description),
  }
}

function nextAvailableExperimentName(preferredName: string, existingNames: Iterable<string>): string {
  const preferred = preferredName.trim() || 'Imported Experiment'
  const names = new Set(existingNames)
  if (!names.has(preferred)) return preferred

  let number = 2
  while (names.has(`${preferred} (${number})`)) number += 1
  return `${preferred} (${number})`
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
  const [nameEdited, setNameEdited] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const experimentsQuery = useQuery({
    queryKey: ['experiments', 'all-for-import'],
    queryFn: () => experimentsApi.list({ includeArchived: true }),
    enabled: open,
  })
  const existingNames = useMemo(
    () => experimentsQuery.data?.map((experiment) => experiment.name) ?? [],
    [experimentsQuery.data],
  )

  useEffect(() => {
    if (!definition || nameEdited || !experimentsQuery.data) return
    setName(nextAvailableExperimentName(definition.sourceName, existingNames))
  }, [definition, existingNames, experimentsQuery.data, nameEdited])

  const importMutation = useMutation({
    mutationFn: () => {
      if (!definition) throw new Error('Choose an experiment definition first.')
      return experimentsApi.importDefinition({
        name: name.trim(),
        description: definition.description,
        hypothesis: definition.hypothesis,
        design_type: definition.designType,
        task_brief: definition.taskBrief,
        design_spec: definition.designSpec,
        graph: definition.graph,
        published_graph: definition.publishedGraph,
        protocol_description: definition.protocolDescription,
      })
    },
    onSuccess: (experiment) => {
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      onOpenChange(false)
      onImported(experiment.id)
    },
    onError: async (error) => {
      // A second tab may create this same name between the lookup above and
      // the atomic insert. Refresh, then offer the next free suffix instead
      // of leaving the user to discover and resolve the collision manually.
      if (!(error instanceof ApiError) || error.status !== 409 || !definition) return
      const experiments = await queryClient.fetchQuery({
        queryKey: ['experiments', 'all-for-import'],
        queryFn: () => experimentsApi.list({ includeArchived: true }),
      })
      setName(nextAvailableExperimentName(definition.sourceName, experiments.map((experiment) => experiment.name)))
      setNameEdited(false)
      importMutation.reset()
    },
  })

  function reset() {
    importMutation.reset()
    setDefinition(null)
    setName('')
    setNameEdited(false)
    setFileName(null)
    setFileError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  function handleOpenChange(next: boolean) {
    if (next) reset()
    onOpenChange(next)
  }

  async function handleFileSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = '' // selecting the same file again should still parse it
    if (!file) return
    importMutation.reset()
    setFileError(null)
    try {
      const parsed = parseDefinition(await file.text())
      setDefinition(parsed)
      setFileName(file.name)
      setNameEdited(false)
      // If the names request has not completed yet, the effect above fills in
      // the collision-safe name as soon as it does.
      setName(nextAvailableExperimentName(parsed.sourceName, existingNames))
    } catch (error) {
      setDefinition(null)
      setFileName(null)
      setName('')
      setFileError(error instanceof Error ? error.message : 'Could not read this file as JSON.')
    }
  }

  const collisionResolved = !!definition && name !== definition.sourceName
  const mutationError = importMutation.error instanceof Error ? importMutation.error.message : null

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create experiment from file</DialogTitle>
          <DialogDescription>
            Import the canvas and experiment definition into a new, editable experiment. Run history and artifact files are not imported.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="experiment-definition-file">Experiment definition</Label>
            <Input
              ref={inputRef}
              id="experiment-definition-file"
              type="file"
              accept="application/json,.json"
              onChange={(event) => void handleFileSelected(event)}
            />
            {fileName && (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <FileJson className="size-3.5" /> {fileName}
              </p>
            )}
          </div>
          {definition && (
            <div className="space-y-1.5">
              <Label htmlFor="import-experiment-name">New experiment name</Label>
              <Input
                id="import-experiment-name"
                value={name}
                onChange={(event) => {
                  setName(event.target.value)
                  setNameEdited(true)
                  importMutation.reset()
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && name.trim()) importMutation.mutate()
                }}
              />
              {collisionResolved && (
                <p className="text-xs text-muted-foreground">
                  An experiment named “{definition.sourceName}” already exists, so a new available name was suggested. You can edit it.
                </p>
              )}
            </div>
          )}
          {fileError && <p className="text-sm text-destructive">{fileError}</p>}
          {importMutation.isError && <p className="text-sm text-destructive">{mutationError ?? 'Could not import this experiment.'}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={importMutation.isPending}>
            Cancel
          </Button>
          <Button onClick={() => importMutation.mutate()} disabled={!definition || !name.trim() || importMutation.isPending}>
            <Upload className="size-4" />
            {importMutation.isPending ? 'Creating…' : 'Create experiment'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
