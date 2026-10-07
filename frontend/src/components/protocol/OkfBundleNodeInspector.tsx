import { nodeAccent } from '@/lib/nodeAccent'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookMarked, RefreshCw, Power, Trash2, Split } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { okfApi } from '@/api/client'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { OkfConceptList } from './OkfConceptList'
import type { OkfBundleNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = nodeAccent('okf_bundle')

// Same model as SkillNodeInspector: no bundle picker. Every OKF Bundle node is
// created by picking a bundle in the OKF bundles browser
// (OkfBundleBrowserPanel/okfCatalog.ts), which pins the server name and tool
// list onto the node -- so a node IS one bundle, and this inspector is "is it
// on, what does it point at, and what's in it". Repointing a node at a
// different bundle contradicts that; add a second node instead.
//
// No per-tool allow-list either, unlike McpToolNodeInspector: an OKF server's
// tools are one coherent read/write set over the same directory, and letting a
// user hand an agent `write_concept` without `read_concept` produces a broken
// bundle, not a useful configuration.
export function OkfBundleNodeInspector({
  node,
  connectorFactorName,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: OkfBundleNodeData }) | null
  connectorFactorName?: string
  onChange: (nodeId: string, data: OkfBundleNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const bundleId = node?.data.config.bundle_id ?? null
  const queryClient = useQueryClient()

  const bundlesQuery = useQuery({ queryKey: ['okf-bundles'], queryFn: () => okfApi.list() })
  // The bundle's own list_concepts output. Fetched from the SERVER rather than
  // rendered from anything cached on the node, because the whole point of a
  // knowledge base is that it changes between runs -- a cached preview would
  // be a snapshot of whenever the node was created.
  const conceptsQuery = useQuery({
    queryKey: ['okf-concepts', bundleId],
    queryFn: () => okfApi.concepts(bundleId!),
    enabled: !!bundleId,
    retry: false,
  })

  // Re-discovers the server's tools and writes them back onto the node, since
  // the node's cached tool_names is what a run actually uses. The one repair
  // path for a bundle that was registered while its server was failing to
  // start.
  const refreshMutation = useMutation({
    mutationFn: () => okfApi.refresh(bundleId!),
    onSuccess: (bundle) => {
      queryClient.invalidateQueries({ queryKey: ['okf-bundles'] })
      queryClient.invalidateQueries({ queryKey: ['okf-concepts', bundleId] })
      if (node) onChange(node.id, { ...node.data, config: { ...node.data.config, tool_names: bundle.tool_names } })
    },
  })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const registered = bundlesQuery.data?.find((b) => b.id === config.bundle_id)

  function patchConfig(patch: Partial<OkfBundleNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
          <BookMarked className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle
            label={data.label}
            placeholder="OKF Bundle"
            onCommit={(label) => onChange(node.id, { ...data, label })}
          />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="rounded-lg border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Knowledge node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon.
          {' '}<span className="ml-4 inline-flex flex-col gap-1 align-middle">
            <span className="text-[10px]">Toolbar preview</span>
            <span role="img" aria-label="Node toolbar preview: activate or deactivate, delete, and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3">
              <Power className="size-3" />
              <Trash2 className="size-3" />
              <Split className="size-3 text-chart-2" />
            </span>
          </span>
        </p>
      </div>
      {connectorFactorName ? <div className="space-y-2 rounded-lg border px-3 py-2"><div className="flex items-center justify-between"><Label>Enabled</Label><Switch checked disabled /></div><p className="text-xs text-muted-foreground">Availability is controlled by connector factor {connectorFactorName}. Remove it to restore individual controls.</p>{Object.entries(bindings).map(([path, name]) => <div key={path} className="space-y-1"><p className="text-xs text-destructive">Individual factor {name} conflicts with this connector.</p><Button variant="outline" size="sm" onClick={() => unbindFactor(path)}>Remove individual binding</Button><p className="text-xs text-muted-foreground">Its declaration remains in Design until you remove or rebind it.</p></div>)}</div> : <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
        <div><Label htmlFor="knowledge-enabled">Enabled</Label><p className="text-xs text-muted-foreground">Off: the wired agent never sees this knowledge at all.</p>{bindings['config.enabled'] && <p className="text-xs text-chart-2">Factor: {bindings['config.enabled']}</p>}</div>
        <Switch id="knowledge-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
      </div>}


      <div className="space-y-1.5">
        <Label>Bundle</Label>
        {bundlesQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : bundlesQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your registered bundles.</p>
        ) : !registered ? (
          // The node names a bundle GET /okf/bundles no longer returns
          // (deregistered, or a protocol imported from another account).
          // Deliberately no dropdown to repoint it -- same as Skill.
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{config.bundle_path ?? 'This node’s bundle'}</span> isn&rsquo;t registered on
            this server. Delete this node and add it again from the OKF bundles panel.
          </p>
        ) : (
          <div className="space-y-2 rounded-lg border px-3 py-2 text-sm">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                {/* The absolute server path, not the generated server name:
                    the path is what the user recognises, and confirming WHERE
                    the agent is about to write is the whole question here. */}
                <p className="truncate font-mono text-xs" title={registered.path ?? ''}>
                  {registered.path ?? '(path unknown)'}
                </p>
                <p className="truncate font-mono text-[11px] text-muted-foreground/70" title={registered.name}>
                  {registered.name}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {registered.status !== 'connected' && (
                  <Badge variant="outline" className="text-destructive">
                    {registered.status}
                  </Badge>
                )}
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Refresh tools"
                  title="Reconnect and re-discover this bundle's tools"
                  disabled={refreshMutation.isPending}
                  onClick={() => refreshMutation.mutate()}
                >
                  <RefreshCw className={`size-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
            {registered.error_message && <p className="text-xs text-destructive">{registered.error_message}</p>}

            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                Tools given to the agent ({config.tool_names.length}) -- the whole set, not a selection: reading and
                writing a bundle only makes sense together.
              </p>
              <div className="flex flex-wrap gap-1">
                {config.tool_names.length === 0 ? (
                  <p className="text-xs text-destructive">
                    None discovered. Refresh above -- until this reports tools, the agent gets nothing from this bundle.
                  </p>
                ) : (
                  config.tool_names.map((name) => (
                    <Badge key={name} variant="outline" className="font-mono text-xs">
                      {name}
                    </Badge>
                  ))
                )}
              </div>
            </div>

            {conceptsQuery.isLoading ? (
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Concepts currently in this bundle</p>
                <Skeleton className="h-20 w-full" />
              </div>
            ) : conceptsQuery.isError ? (
              <p className="text-xs text-destructive">Could not reach this bundle&rsquo;s server.</p>
            ) : (
              // is_error is passed through, not just the transport failure
              // above it: the server can answer while the tool itself failed
              // (its text is then the exception, not a payload), and that reads
              // as an empty bundle unless it is shown as the error it is.
              <OkfConceptList
                content={conceptsQuery.data?.content ?? ''}
                isError={conceptsQuery.data?.is_error ?? false}
              />
            )}
          </div>
        )}
      </div>
    </NodeInspectorDialog>
  )
}
