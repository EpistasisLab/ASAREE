import { useState } from 'react'
import { Download } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import type { Protocol } from '@/types/protocols'

export function ExperimentDefinitionDownload({ experimentId, protocol }: { experimentId: string; protocol: Protocol }) {
  const [downloadingDefinition, setDownloadingDefinition] = useState(false)
  const [definitionDownloadError, setDefinitionDownloadError] = useState<string | null>(null)
  async function downloadDefinition() {
    if (!protocol) return
    setDownloadingDefinition(true)
    setDefinitionDownloadError(null)
    try {
      // Read fresh rather than exporting whichever React Query snapshot
      // happens to be on screen. A portable definition should describe one
      // coherent point in time, including the currently published canvas.
      const [experiment, canvas, designRevisions] = await Promise.all([
        experimentsApi.get(experimentId),
        protocolsApi.get(protocol.id),
        experimentsApi.listDesignRevisions(experimentId),
      ])
      const revisionIds = [...new Set([canvas.published_revision_id, experiment.locked_protocol_revision_id].filter((id): id is string => !!id))]
      const revisions = await Promise.all(revisionIds.map((revisionId) => protocolsApi.getRevision(canvas.id, revisionId)))
      const revisionById = new Map(revisions.map((revision) => [revision.id, revision]))
      const publishedCanvas = canvas.published_revision_id ? revisionById.get(canvas.published_revision_id) ?? null : null
      const lockedCanvas = experiment.locked_protocol_revision_id ? revisionById.get(experiment.locked_protocol_revision_id) ?? null : null
      const payload = {
        format: 'asaree.experiment-definition',
        schema_version: 1,
        exported_at: new Date().toISOString(),
        // The existing canvas importer consumes these two top-level fields.
        // Keep them as the current draft while the richer envelope below
        // carries immutable published/locked snapshots and provenance.
        name: experiment.name,
        description: canvas.description,
        graph: canvas.graph,
        design_spec: experiment.design_spec,
        measurement_plan: experiment.measurement_plan,
        experiment: {
          source_id: experiment.id,
          name: experiment.name,
          description: experiment.description,
          hypothesis: experiment.hypothesis,
          design_type: experiment.design_type,
          task_brief: experiment.task_brief,
          design_spec: experiment.design_spec,
          measurement_plan: experiment.measurement_plan,
          dataset_ids: experiment.dataset_ids,
          archived_at: experiment.archived_at,
          created_at: experiment.created_at,
          updated_at: experiment.updated_at,
          lock: experiment.locked_at ? {
            locked_at: experiment.locked_at,
            source_protocol_revision_id: experiment.locked_protocol_revision_id,
            design_spec: experiment.locked_design_spec,
            measurement_plan: experiment.locked_measurement_plan,
            canvas_revision: lockedCanvas ? {
              source_id: lockedCanvas.id,
              revision: lockedCanvas.revision,
              published_at: lockedCanvas.published_at,
              graph: lockedCanvas.graph,
            } : null,
          } : null,
        },
        canvas: {
          source_protocol_id: canvas.id,
          name: canvas.name,
          description: canvas.description,
          draft: { graph: canvas.graph, updated_at: canvas.updated_at },
          published: publishedCanvas ? {
            source_id: publishedCanvas.id,
            revision: publishedCanvas.revision,
            published_at: publishedCanvas.published_at,
            graph: publishedCanvas.graph,
          } : null,
          has_unpublished_changes: canvas.has_unpublished_changes,
        },
        design_revisions: designRevisions.map((revision) => ({
          source_id: revision.id,
          revision: revision.revision,
          superseded_at: revision.superseded_at,
          design_spec: revision.design_spec,
          cell_count: revision.cell_count,
          replicate_count: revision.replicate_count,
          scored_replicate_count: revision.scored_replicate_count,
          created_at: revision.created_at,
        })),
        portability: {
          excluded: ['provider credential secrets', 'replicate results and run history', 'dataset file contents', 'knowledge bundle/document contents', 'skill file contents'],
          note: 'All canvas nodes and their configuration are included. Dataset, Knowledge, and Skill nodes retain their labels and resource IDs as references; recreate or map their external artifacts before importing into another workspace.',
        },
      }
      const name = experiment.name || canvas.name
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${name.trim().replace(/[^a-z0-9]+/gi, '-').replace(/(^-|-$)/g, '') || 'experiment'}.json`
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      setDefinitionDownloadError('Could not prepare the experiment definition. Please try again.')
    } finally {
      setDownloadingDefinition(false)
    }
  }

  return <><Button type="button" variant="outline" size="xs" onClick={downloadDefinition} disabled={downloadingDefinition}>
    <Download className="size-3.5" /> {downloadingDefinition ? 'Preparing definition…' : 'Download experiment definition'}
  </Button>{definitionDownloadError && <p role="alert" className="text-xs text-destructive">{definitionDownloadError}</p>}</>
}
