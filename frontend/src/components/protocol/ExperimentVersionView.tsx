import { useState } from 'react'
import { Background, ReactFlow, type NodeTypes, type NodeProps } from '@xyflow/react'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { NODE_TYPES } from './protocolNodeTypes'
import { ProtocolCanvasActionsProvider } from './ProtocolCanvasContext'
import type { ProtocolRevision } from '@/types/protocols'
import { datasetRowBindings } from '@/lib/datasetRows'
import type { ResultsSelection } from './ResultsTab'
import { ExecutionBrowser } from './ExecutionBrowser'

// Historical graphs have no editing callbacks or autosave effects. Keep them
// outside ProtocolCanvas so browsing can never write into the draft cache.
const READ_ONLY_NODE_TYPES = Object.fromEntries(Object.entries(NODE_TYPES as NodeTypes).map(([kind, Renderer]) => [kind, (props: NodeProps) => <div className="pointer-events-none"><Renderer {...props} /></div>])) as NodeTypes
const noop = () => {}
const READ_ONLY_ACTIONS = { experimentLocked: true, requestConnectorAdd: noop, requestMainEdgeAdd: noop, requestEdgeInsert: noop, requestRunNode: noop, requestMakeFactor: noop, requestEditFactor: noop, metricsForNode: () => [], convertLegacyOutputContract: noop }

export function ExperimentVersionCanvas({ version }: { version: ProtocolRevision }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selected = version.graph.nodes.find(node => node.id === selectedId)
  return <ProtocolCanvasActionsProvider value={READ_ONLY_ACTIONS}><ReactFlow
    nodeTypes={READ_ONLY_NODE_TYPES}
    nodes={version.graph.nodes}
    edges={version.graph.edges.map(edge => ({ ...edge, sourceHandle: edge.sourceHandle ?? undefined, targetHandle: edge.targetHandle ?? undefined }))}
    onNodeClick={(_, node) => setSelectedId(node.id)}
    nodesDraggable={false} nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null} fitView
    proOptions={{ hideAttribution: true }}
  ><Background color="var(--primary)" gap={28} style={{ opacity: 0.2 }} /></ReactFlow>
    <Dialog open={!!selected} onOpenChange={open => { if (!open) setSelectedId(null) }}><DialogContent><DialogHeader><DialogTitle>{String(selected?.data.label ?? selected?.id)} · Version {version.revision}</DialogTitle></DialogHeader><p className="text-xs text-muted-foreground">Saved settings · read-only</p><pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words font-mono text-xs">{JSON.stringify(selected?.data, null, 2)}</pre></DialogContent></Dialog>
  </ProtocolCanvasActionsProvider>
}

export function ExperimentVersionDesign({ version }: { version: ProtocolRevision }) {
  const snapshot = version.experiment_snapshot
  if (!snapshot) return <p className="p-3 text-xs text-muted-foreground">This legacy publication saved only its canvas. Its original design settings were not captured.</p>
  const spec = snapshot.design_spec
  return <div className="space-y-3 p-3">
    <p className="text-xs text-muted-foreground">Experiment version {version.revision} · read-only</p>
    {snapshot.hypothesis && <Card className="gap-2 p-3"><p className="text-sm font-medium">Hypothesis</p><p className="whitespace-pre-wrap text-xs">{snapshot.hypothesis}</p></Card>}
    <Card className="gap-2 p-3"><p className="text-sm font-medium">Factors</p>{spec?.factors?.length ? spec.factors.map(factor => <div key={factor.name} className="space-y-1 text-xs"><p>{factor.name}</p><p className="break-words font-mono text-muted-foreground">{factor.levels.map((level, index) => factor.level_labels?.[index] ?? (typeof level === 'string' ? level : JSON.stringify(level))).join(' · ')}</p></div>) : <p className="text-xs text-muted-foreground">No factors declared.</p>}<p className="font-mono text-xs">{spec?.replicates ?? 1} replicates per cell</p></Card>
    <Card className="gap-2 p-3"><p className="text-sm font-medium">Execution settings</p><p className="text-xs">Design: {snapshot.design_type ?? 'Not specified'}</p><p className="text-xs">Coordination: {spec?.coordination_strategy?.slug ?? 'sequential'}</p><p className="font-mono text-xs">Randomization seed: {spec?.randomization_seed ?? 'Not set'}</p></Card>
    <Card className="gap-2 p-3"><p className="text-sm font-medium">Dataset execution</p>{datasetRowBindings(version.graph).map(binding => <div key={binding.edge.id} className="text-xs"><p>{binding.config?.dataset_name ?? 'Dataset'} → {String(version.graph.nodes.find(node => node.id === binding.edge.target)?.data.label ?? binding.edge.target)}</p><p className="text-muted-foreground">{binding.input.mode === 'per_row' ? `One execution per row · columns: ${binding.input.columns.join(', ')}` : 'Whole dataset · one execution per replicate'}</p></div>)}{!datasetRowBindings(version.graph).length && <p className="text-xs text-muted-foreground">No dataset inputs connected.</p>}</Card>
    <Card className="gap-2 p-3"><p className="text-sm font-medium">Metrics</p>{(snapshot.measurement_plan?.metrics ?? spec?.metrics ?? []).map(metric => <div key={metric.name} className="text-xs"><p>{metric.name}</p><p className="text-muted-foreground">{metric.direction ?? 'maximize'} · {metric.aggregation ?? 'mean'}</p></div>)}{!(snapshot.measurement_plan?.metrics?.length || spec?.metrics?.length) && <p className="text-xs text-muted-foreground">No metrics declared.</p>}</Card>
    {!!snapshot.measurement_plan?.producers.length && <Card className="gap-2 p-3"><p className="text-sm font-medium">Measurement producers</p>{snapshot.measurement_plan.producers.map(producer => <div key={producer.id} className="text-xs"><p>{producer.producer_id} · {producer.kind}</p><p className="font-mono text-muted-foreground">{Object.entries(producer.outputs).map(([key, value]) => `${key} → ${value}`).join(' · ')}</p></div>)}</Card>}
    {snapshot.task_brief && <Card className="gap-2 p-3"><p className="text-sm font-medium">Task brief</p><p className="whitespace-pre-wrap text-xs">{typeof snapshot.task_brief === 'string' ? snapshot.task_brief : JSON.stringify(snapshot.task_brief, null, 2)}</p></Card>}
  </div>
}

export function ExperimentVersionRuns({ experimentId, protocolId, version, onSelectResult, focus, onClearFocus }: { experimentId: string; protocolId?: string; version: ProtocolRevision; onSelectResult: (selection: ResultsSelection) => void; focus?: import('@/types/executionResults').ExecutionFocus; onClearFocus?: () => void }) {
  const scope = { protocol_id: protocolId, protocol_revision_id: version.id }
  return <ExecutionBrowser key={`${version.id}:${JSON.stringify(focus)}`} experimentId={experimentId} scope={scope} mode="runs" onSelect={onSelectResult} focus={focus} onClearFocus={onClearFocus} />
}
