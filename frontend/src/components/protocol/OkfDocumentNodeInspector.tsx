import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, RefreshCw } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { okfApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { OkfDocumentNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('okf_document')

// The mirror of OkfBundleNodeInspector, and the same two refusals: no picker
// to repoint the node at a different document (a node IS one document -- add a
// second node), and no per-tool allow-list (an OKF server's read/write tools
// over one concept are a set, and splitting them yields a broken document
// rather than a configuration).
//
// The preview differs, though: a bundle previews its list of concepts, while a
// document IS one concept, so this shows the file's current text. That's the
// whole reason to open this inspector after a run -- the agent rewrites the
// document as it works, and this is where you see what it became.
export function OkfDocumentNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: OkfDocumentNodeData }) | null
  experimentId: string | null
  factorNodeLabel: string
  onChange: (nodeId: string, data: OkfDocumentNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const documentId = node?.data.config.document_id ?? null
  const queryClient = useQueryClient()

  const documentsQuery = useQuery({ queryKey: ['okf-documents'], queryFn: () => okfApi.listDocuments() })
  // Read off disk on open, never from anything cached on the node: the agent
  // rewrites this file during a run, so a cached copy would show the upload
  // rather than the knowledge.
  const markdownQuery = useQuery({
    queryKey: ['okf-document-markdown', documentId],
    queryFn: () => okfApi.documentMarkdown(documentId!),
    enabled: !!documentId,
    retry: false,
  })

  const refreshMutation = useMutation({
    mutationFn: () => okfApi.refreshDocument(documentId!),
    onSuccess: (document) => {
      queryClient.invalidateQueries({ queryKey: ['okf-documents'] })
      queryClient.invalidateQueries({ queryKey: ['okf-document-markdown', documentId] })
      if (node) onChange(node.id, { ...node.data, config: { ...node.data.config, tool_names: document.tool_names } })
    },
  })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const registered = documentsQuery.data?.find((d) => d.id === config.document_id)

  function patchConfig(patch: Partial<OkfDocumentNodeData['config']>) {
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
          <FileText className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle
            label={data.label}
            placeholder="OKF Document"
            onCommit={(label) => onChange(node.id, { ...data, label })}
          />
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
              <Label htmlFor="okf-document-enabled" className="flex items-center gap-1.5">
                Enabled
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">
                Off: the wired agent gets none of this document&rsquo;s tools, so it can neither read nor write it.
              </p>
            </div>
            <Switch
              id="okf-document-enabled"
              checked={config.enabled ?? true}
              onCheckedChange={(checked) => patchConfig({ enabled: checked })}
            />
          </div>
        )}
      </FactorBindableField>

      <div className="space-y-1.5">
        <Label>Document</Label>
        {documentsQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : documentsQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your uploaded documents.</p>
        ) : !registered ? (
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{config.document_title ?? 'This node’s document'}</span> isn&rsquo;t stored on
            this server. Delete this node and upload the file again from the OKF documents panel.
          </p>
        ) : (
          <div className="space-y-2 rounded-lg border px-3 py-2 text-sm">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                {/* The LIVE title, not the node's snapshot label: if the agent
                    has retitled the concept, that difference is exactly what
                    you opened this to find out. */}
                <p className="truncate text-xs" title={registered.title ?? ''}>
                  {registered.title ?? '(no title in the frontmatter)'}
                </p>
                <p
                  dir="rtl"
                  className="truncate text-left font-mono text-[11px] text-muted-foreground/70"
                  title={registered.path ?? ''}
                >
                  {registered.path ?? '(file missing)'}
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
                  title="Reconnect and re-discover this document's tools"
                  disabled={refreshMutation.isPending}
                  onClick={() => refreshMutation.mutate()}
                >
                  <RefreshCw className={`size-3.5 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
                </Button>
              </div>
            </div>
            {registered.error_message && <p className="text-xs text-destructive">{registered.error_message}</p>}

            {(registered.concept_type || registered.tags.length > 0) && (
              <div className="flex flex-wrap gap-1">
                {registered.concept_type && (
                  <Badge variant="secondary" className="font-mono text-xs">
                    {registered.concept_type}
                  </Badge>
                )}
                {registered.tags.map((tag) => (
                  <Badge key={tag} variant="outline" className="font-mono text-xs">
                    {tag}
                  </Badge>
                ))}
              </div>
            )}

            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                Tools given to the agent ({config.tool_names.length}) -- the whole set, not a selection: reading and
                writing a concept only makes sense together.
              </p>
              <div className="flex flex-wrap gap-1">
                {config.tool_names.length === 0 ? (
                  <p className="text-xs text-destructive">
                    None discovered. Refresh above -- until this reports tools, the agent gets nothing from this document.
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

            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">The document as it stands now</p>
              {markdownQuery.isLoading ? (
                <Skeleton className="h-20 w-full" />
              ) : markdownQuery.isError ? (
                <p className="text-xs text-destructive">Could not read this document&rsquo;s file.</p>
              ) : (
                <pre className="max-h-[calc(100vh-30rem)] min-h-24 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[0.7rem] whitespace-pre-wrap">
                  {markdownQuery.data?.markdown || '(empty)'}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </NodeInspectorDialog>
  )
}
