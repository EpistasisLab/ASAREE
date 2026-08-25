import { useState } from 'react'
import { nodeAccent } from '@/lib/nodeAccent'
import { useQuery } from '@tanstack/react-query'
import { Database } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { datasetsApi } from '@/api/client'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { SplitDatasetDialog } from './SplitDatasetDialog'
import type { DatasetNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('dataset')

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

// The stored fraction shown the way it was asked for: 0.2 -> "20%", 0.155 ->
// "15.5%". Trailing zeros are trimmed rather than fixed to a set precision so
// the common case reads as a round number, and this is the REQUESTED fraction
// -- a group-aware split lands near it, not exactly on it.
function formatTestSize(fraction: number): string {
  return `${+(fraction * 100).toFixed(2)}%`
}

// Same floating-dialog shell as McpToolNodeInspector, but with NO "which
// dataset" picker: the dataset IS the node, chosen when it was added from the
// canvas's Datasets browser (DatasetBrowserPanel/nodeDataForDataset), the same
// model as Skill and the server-dedicated MCP node types. So this is a
// read-out of the bound dataset -- description, target column, hashes, split
// state, data dictionary -- plus the two things that ARE this node's to
// change: its Enabled switch (factor-bindable) and its split.
//
// Which is also why the experiment's own attached-dataset list is synced from
// the canvas's Dataset nodes rather than here (ProtocolCanvas's
// syncExperimentDatasets effect): with no picker, the binding can no longer
// change from inside this dialog.
//
// The one exception to "no picker here" is the title row's "Make factor"
// button: a dataset_config factor's levels are whole Dataset configs, so
// picking among registered datasets happens in FactorEditorDialog's own
// DatasetConfigLevelRow. That's the supported way to run one experiment
// across several datasets -- the Dataset connector itself is capped at one
// node per agent, since a cell's workspace holds exactly one dataset.
//
// A dataset_id in an imported protocol JSON is per-account/environment (same
// reasoning as an MCP Tool node's server_id), so an imported node can name a
// dataset this account doesn't have; the Description section says so and
// nodeConfigIssues flags it. The fix is to delete the node and add the right
// dataset from the browser -- there's nothing here to repoint.
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
  const [splitDialogOpen, setSplitDialogOpen] = useState(false)
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: () => datasetsApi.list() })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedDataset = datasetsQuery.data?.find((d) => d.id === config.dataset_id)
  const isQuickSplit = selectedDataset?.split_method === 'quick'

  function patchConfig(patch: Partial<DatasetNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
          {/* In the title row rather than beside a field, because there is no
              "which dataset" field here to sit beside -- the dataset IS the
              node. Same position as the Agent/Pattern inspectors' own
              MakeNodeFactorButton, and the same visual identity, but this one
              binds one specific field (`config`, the whole node) directly
              instead of opening a per-node field picker: a Dataset node has
              exactly one whole-node factor worth making, so a picker listing
              one entry would be a step with no choice in it. */}
          <FactorBindableField
            experimentId={experimentId}
            fieldPath="config"
            defaultLabel="Dataset"
            nodeLabel={factorNodeLabel}
            levelType="dataset_config"
            currentValue={config}
            boundFactorName={bindings.config}
            onBind={(name) => bindFactor('config', name)}
            onUnbind={() => unbindFactor('config')}
          >
            {(trigger) => trigger}
          </FactorBindableField>
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
          <Label>Description</Label>
          {selectedDataset ? (
            // Wraps rather than truncating -- this is the one place the whole
            // description is readable; DatasetBrowserPanel's rows show a
            // one-line version. Read-only: there's no PATCH /datasets, so a
            // description is set once, at registration.
            <p className="text-sm whitespace-pre-wrap text-muted-foreground">
              {selectedDataset.description || 'No description was given when this dataset was registered.'}
            </p>
          ) : (
            // The node names a dataset this account doesn't have -- an
            // imported protocol JSON (a dataset_id is per-account, same as an
            // MCP Tool node's server_id), or one deleted since. There's no
            // picker here to repoint it: the dataset IS the node, so the fix
            // is to delete this one and add the right dataset from the
            // canvas's Datasets browser (nodeConfigIssues flags it as a
            // config error meanwhile).
            <p className="text-sm text-muted-foreground">
              {config.dataset_id
                ? 'This node names a dataset that is not in your library. Delete it and add the dataset you want from the canvas’s Datasets browser.'
                : 'No dataset selected. Delete this node and add one from the canvas’s Datasets browser.'}
            </p>
          )}
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
          {/* No description here -- it has its own section above now. */}
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
            {/* The hashes above identify WHICH files the split produced; these
                describe HOW, which is what makes it reproducible/reviewable.
                Only rendered for a quick split -- a manual one was computed
                outside ASAREE, so it has no parameters to show, and a split
                made before these were recorded has none either (see
                RegisteredDataset's own comments). Both cases fall through to
                the note below rather than printing misleading defaults. */}
            {isQuickSplit && (
              <>
                <div>
                  <p className="text-muted-foreground">Group column</p>
                  {/* Null on a quick split is meaningful, not missing: the
                      backend stores the column it ACTUALLY grouped on, so no
                      column means the holdout was stratified instead. */}
                  <p className="truncate font-mono" title={selectedDataset.split_group_column ?? undefined}>
                    {selectedDataset.split_group_column ?? 'none (stratified)'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Test size</p>
                  <p className="truncate font-mono">
                    {selectedDataset.split_test_size != null ? formatTestSize(selectedDataset.split_test_size) : '—'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground">Seed</p>
                  <p className="truncate font-mono">{selectedDataset.split_seed ?? '—'}</p>
                </div>
              </>
            )}
          </div>
          {selectedDataset.train_path && !isQuickSplit && (
            <p className="text-xs text-muted-foreground">
              {selectedDataset.split_method === 'manual'
                ? 'Split uploaded as a ready-made train/test pair — its group column, test size and seed were decided outside ASAREE.'
                : 'This split predates ASAREE recording split parameters. Re-split to capture its group column, test size and seed.'}
            </p>
          )}
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

      {selectedDataset && <SplitDatasetDialog open={splitDialogOpen} onOpenChange={setSplitDialogOpen} dataset={selectedDataset} />}
    </NodeInspectorDialog>
  )
}
