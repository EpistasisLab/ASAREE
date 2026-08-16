import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Database, Plus } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { datasetsApi, experimentsApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import type { Dataset } from '@/types/datasets'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { RegisterDatasetDialog } from './RegisterDatasetDialog'
import { SplitDatasetDialog } from './SplitDatasetDialog'
import type { DatasetNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('dataset')

interface DictionaryColumn {
  name?: string
  type?: string
}

interface ParsedDictionary {
  n_rows?: number
  target?: string
  columns?: DictionaryColumn[]
}

// dictionary_json is opaque to ASAREE's own DB/API (see RegisteredDataset's
// own comment) -- the shape read here (n_rows/target/columns[].type) is
// ARES's own de facto contract (see asaree-spinal-use-case's
// spine_data_dictionary.json), read best-effort for a nicer summary than a
// raw JSON dump (row/column counts, target, a per-type breakdown -- ARES's
// own dataset page showed the same, never the raw dictionary itself), never
// enforced -- a dictionary missing `columns` (or not valid JSON at all)
// just means this summary silently has less to show, not an error.
function parseDictionary(raw: string): ParsedDictionary | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed !== null && typeof parsed === 'object' ? (parsed as ParsedDictionary) : null
  } catch {
    return null
  }
}

// Same floating-dialog shell as McpToolNodeInspector -- "which registered
// dataset" is the one real parameter, resolved from the caller's own
// registered datasets (GET /datasets), not hand-typed. Picking one both
// updates this node's own config AND immediately syncs the linked
// experiment's real dataset_id FK (a fire-and-forget PATCH, matching
// FactorBindableField's own immediate-persist convention) -- so the two
// never drift apart. A dataset_id in an imported protocol JSON is per-
// account/environment (same reasoning as an MCP Tool node's server_id): a
// freshly-imported Dataset node just shows "Select a dataset…" until the
// importing user picks the real one, at which point this same sync fires
// naturally -- no special import-time handling needed.
export function DatasetNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: DatasetNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's
  // agentTracedLabel) -- distinct from data.label, which is this node's own
  // plain label shown in the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: DatasetNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  const [splitDialogOpen, setSplitDialogOpen] = useState(false)
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: () => datasetsApi.list() })
  const syncExperimentDataset = useMutation({
    mutationFn: (datasetId: string) => experimentsApi.update(experimentId!, { dataset_id: datasetId }),
  })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedDataset = datasetsQuery.data?.find((d) => d.id === config.dataset_id)

  function patchConfig(patch: Partial<DatasetNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  // Shared by the Select's own onValueChange and RegisterDatasetDialog's
  // onCreated -- picking an existing dataset and registering a brand new one
  // both end the same way: this node's config AND the linked experiment's
  // real dataset_id FK both point at it.
  function selectDataset(dataset: Dataset) {
    patchConfig({ dataset_id: dataset.id, dataset_name: dataset.name })
    if (experimentId) syncExperimentDataset.mutate(dataset.id)
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(node!.id, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(node!.id, { ...data, factor_bindings: next })
  }

  return (
    <NodeInspectorDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      accent={ACCENT}
      title={
        <>
          <Database className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Dataset" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.enabled"
        defaultLabel="Enabled"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.enabled']}
        onBind={(name) => bindFactor('config.enabled', name)}
        onUnbind={() => unbindFactor('config.enabled')}
      >
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="dataset-enabled" className="flex items-center gap-1.5">
                Enabled
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">Off: no dataset context is given to the wired agent.</p>
            </div>
            <Switch id="dataset-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
          </div>
        )}
      </FactorBindableField>

      {datasetsQuery.isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : datasetsQuery.isError ? (
        <p className="text-sm text-destructive">Could not load your registered datasets.</p>
      ) : (
        <div className="space-y-1.5">
          <Label>Dataset</Label>
          {!datasetsQuery.data || datasetsQuery.data.length === 0 ? (
            <p className="text-sm text-muted-foreground">No datasets registered yet.</p>
          ) : (
            <Select
              value={config.dataset_id ?? '__none__'}
              onValueChange={(value) => {
                if (!value || value === '__none__') return
                const dataset = datasetsQuery.data.find((d) => d.id === value)
                if (dataset) selectDataset(dataset)
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue>{() => selectedDataset?.name ?? 'Select a dataset…'}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" disabled>
                  Select a dataset…
                </SelectItem>
                {datasetsQuery.data.map((dataset) => (
                  <SelectItem key={dataset.id} value={dataset.id}>
                    {dataset.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
            <Plus className="size-3.5" /> Register new dataset
          </Button>
        </div>
      )}

      {selectedDataset && (
        <div className="space-y-2 rounded-lg border px-3 py-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate font-medium" title={selectedDataset.name}>
              {selectedDataset.name}
            </p>
            {selectedDataset.created_at && (
              <span
                className="shrink-0 text-xs text-muted-foreground"
                title={new Date(selectedDataset.created_at).toLocaleString()}
              >
                {new Date(selectedDataset.created_at).toLocaleDateString()}
              </span>
            )}
          </div>
          {selectedDataset.description && <p className="text-xs text-muted-foreground">{selectedDataset.description}</p>}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <p className="text-muted-foreground">Target column</p>
              <p className="truncate font-mono">{selectedDataset.target_column ?? '—'}</p>
            </div>
            {selectedDataset.train_sha256 && selectedDataset.test_sha256 && (
              <div>
                <p className="text-muted-foreground">Train / test hash</p>
                <p
                  className="truncate font-mono"
                  title={`train: ${selectedDataset.train_sha256}\ntest: ${selectedDataset.test_sha256}`}
                >
                  {selectedDataset.train_sha256.slice(0, 10)}… / {selectedDataset.test_sha256.slice(0, 10)}…
                </p>
              </div>
            )}
          </div>
          {selectedDataset.train_path ? (
            <Button variant="outline" size="sm" onClick={() => setSplitDialogOpen(true)}>
              Re-split
            </Button>
          ) : (
            <div className="flex items-center justify-between gap-2 rounded-md border border-dashed px-2.5 py-1.5">
              <p className="text-xs text-muted-foreground">Not split yet -- can't be used in a workspace until it is.</p>
              <Button variant="outline" size="sm" onClick={() => setSplitDialogOpen(true)}>
                Split dataset
              </Button>
            </div>
          )}
          {selectedDataset.dictionary_json &&
            (() => {
              const dictionary = parseDictionary(selectedDataset.dictionary_json)
              const columns = dictionary?.columns?.filter((c): c is DictionaryColumn => !!c && typeof c === 'object') ?? []
              if (columns.length === 0) {
                return <p className="text-xs text-muted-foreground">Data dictionary present, but not in a recognized format.</p>
              }
              const typeCounts = columns.reduce<Record<string, number>>((acc, c) => {
                const type = c.type || 'Unknown'
                acc[type] = (acc[type] ?? 0) + 1
                return acc
              }, {})
              return (
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground">Data dictionary</p>
                  <div className="flex flex-wrap items-center gap-1.5 font-mono text-xs">
                    {dictionary?.n_rows != null && <Badge variant="outline">{dictionary.n_rows.toLocaleString()} rows</Badge>}
                    <Badge variant="outline">{columns.length} columns</Badge>
                    {dictionary?.target && <Badge variant="outline">Target: {dictionary.target}</Badge>}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(typeCounts).map(([type, count]) => (
                      <Badge key={type} variant="outline" className="font-mono text-xs">
                        {type}: {count}
                      </Badge>
                    ))}
                  </div>
                </div>
              )
            })()}
        </div>
      )}

      <RegisterDatasetDialog
        open={registerDialogOpen}
        onOpenChange={setRegisterDialogOpen}
        onCreated={(dataset) => selectDataset(dataset)}
      />
      {selectedDataset && <SplitDatasetDialog open={splitDialogOpen} onOpenChange={setSplitDialogOpen} dataset={selectedDataset} />}
    </NodeInspectorDialog>
  )
}
