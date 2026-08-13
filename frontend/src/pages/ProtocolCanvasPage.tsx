import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { Link, useParams } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { ProtocolCanvas } from '@/components/protocol/ProtocolCanvas'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'

// Click-to-rename, n8n's own pattern for a workflow created with a
// placeholder name: no gate before creating, edit the name in place once
// you're looking at what you're naming.
function EditableExperimentName({ experiment }: { experiment: Experiment }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(experiment.name)
  const queryClient = useQueryClient()

  const renameMutation = useMutation({
    mutationFn: (name: string) => experimentsApi.update(experiment.id, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
    },
  })

  function commit() {
    setEditing(false)
    const trimmed = value.trim()
    if (trimmed && trimmed !== experiment.name) renameMutation.mutate(trimmed)
    else setValue(experiment.name)
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
            setValue(experiment.name)
            setEditing(false)
          }
        }}
        className="h-8 w-72 text-lg font-semibold"
      />
    )
  }

  return (
    <button
      type="button"
      onClick={() => {
        setValue(experiment.name)
        setEditing(true)
      }}
      title="Click to rename"
      className="rounded-md px-1.5 py-0.5 -ml-1.5 text-lg font-semibold tracking-tight hover:bg-muted"
    >
      {experiment.name}
    </button>
  )
}

// Shows what the canvas's "+ Make experimental factor" bindings have
// produced so far -- N factors on the linked experiment's design_spec, and
// the cross-product size that "Generate design" would materialize (client
// computed, mirrors services.design_generation's own combinatorics). Hidden
// entirely once there are no factors yet, same as CellsSection being gated
// behind design_type === 'factorial' -- nothing to preview or generate.
function DesignPreview({ experiment }: { experiment: Experiment }) {
  const queryClient = useQueryClient()
  const factors = experiment.design_spec?.factors ?? []
  const combinations = factors.reduce((acc, f) => acc * Math.max(f.levels.length, 1), 1)

  const generateMutation = useMutation({
    mutationFn: () => experimentsApi.generateDesign(experiment.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'cells'] })
    },
  })

  if (factors.length === 0) return null

  return (
    <div className="flex items-center gap-2 rounded-md border bg-card px-3 py-1.5">
      <span className="font-mono text-xs text-muted-foreground">
        {factors.length} factor{factors.length === 1 ? '' : 's'} → {combinations} combination{combinations === 1 ? '' : 's'}
      </span>
      <Button size="sm" variant="outline" disabled={generateMutation.isPending} onClick={() => generateMutation.mutate()}>
        {generateMutation.isPending ? 'Generating…' : 'Generate design'}
      </Button>
      {generateMutation.data && (
        <span className="text-xs text-muted-foreground">{generateMutation.data.length} cell(s) total</span>
      )}
    </div>
  )
}

// One protocol per experiment is a V1 UX convention enforced here (find the
// first protocol tagged with this experiment, or lazily create one), not a
// schema constraint -- Protocol.experiment_id is nullable and a protocol is
// a standalone reusable object, so a future protocol-library page can
// change this without a migration.
export function ProtocolCanvasPage() {
  const { experimentId } = useParams<{ experimentId: string }>()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })

  const protocolQuery = useQuery({
    queryKey: ['protocols', 'for-experiment', experimentId],
    queryFn: async () => {
      const existing = await protocolsApi.list(experimentId!)
      if (existing.length > 0) return existing[0]
      const experiment = await experimentsApi.get(experimentId!)
      return protocolsApi.create({ name: `Protocol: ${experiment.name}`, experiment_id: experimentId! })
    },
    enabled: !!experimentId,
  })

  return (
    <div className="flex h-svh flex-col bg-muted/30">
      <AppHeader />

      <main className="flex flex-1 flex-col gap-3 overflow-hidden px-6 py-6">
        <div className="flex items-center gap-3">
          <Link to={`/experiments/${experimentId}`} className="text-sm text-muted-foreground hover:underline">
            ← Experiment
          </Link>
          {experimentQuery.data && <EditableExperimentName experiment={experimentQuery.data} />}
          <div className="flex-1" />
          {experimentQuery.data && <DesignPreview experiment={experimentQuery.data} />}
        </div>

        {protocolQuery.isLoading ? (
          <Skeleton className="flex-1" />
        ) : protocolQuery.isError || !protocolQuery.data ? (
          <p className="text-sm text-muted-foreground">Could not load this experiment's protocol.</p>
        ) : (
          <Card className="flex-1 overflow-hidden p-0">
            <ReactFlowProvider>
              <ProtocolCanvas
                key={protocolQuery.data.id}
                protocolId={protocolQuery.data.id}
                experimentId={protocolQuery.data.experiment_id}
                initialGraph={protocolQuery.data.graph}
              />
            </ReactFlowProvider>
          </Card>
        )}
      </main>
    </div>
  )
}
