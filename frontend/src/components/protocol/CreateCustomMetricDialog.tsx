import { useCallback, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { experimentsApi } from '@/api/client'
import { agentOutputBindingForMetric } from '@/lib/agentOutputMetrics'
import { applyCustomMetricChange, type CustomMetricProducerConfig, type CustomMetricSourceContext } from '@/lib/customMetrics'
import { makeCustomMetric, normalizeDesignMetrics } from '@/lib/metricCatalog'
import { mcpToolBindingForMetric } from '@/lib/mcpToolMetrics'
import { pythonScriptBindingForMetric } from '@/lib/pythonScriptMetrics'
import type { ProtocolGraph } from '@/types/protocols'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CustomMetricFlow } from './CustomMetricDialogs'
import { NODE_INSPECTOR_CONTENT_CLASSNAME } from './NodeInspectorDialog'

export function CreateCustomMetricDialog({
  experimentId,
  protocolId,
  graph,
  sourceContext,
  metricId,
  open,
  onOpenChange,
}: {
  experimentId: string
  protocolId: string
  graph: ProtocolGraph
  sourceContext: CustomMetricSourceContext
  metricId?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId),
    enabled: open,
  })
  const experiment = experimentQuery.data
  const existingMetrics = normalizeDesignMetrics(experiment?.design_spec?.metrics)
  const existingMetric = existingMetrics.find((metric) => metric.id === metricId)
  const existingBinding = agentOutputBindingForMetric(experiment?.measurement_plan ?? null, metricId)
    ?? pythonScriptBindingForMetric(experiment?.measurement_plan ?? null, metricId)
    ?? mcpToolBindingForMetric(experiment?.measurement_plan ?? null, metricId)
  const [draft] = useState(() => makeCustomMetric({
    name: '',
    description: '',
  }))
  const flushAutosaveRef = useRef<(() => void) | null>(null)
  const createMutation = useMutation({
    mutationFn: async ({ metric, config }: { metric: typeof draft; config: CustomMetricProducerConfig }) => {
      // Re-read immediately before the replacement PATCH. Design metadata and
      // its measurement plan are nested documents, so this preserves edits
      // made elsewhere while the metric dialog was open.
      const fresh = await experimentsApi.get(experimentId)
      const currentMetrics = normalizeDesignMetrics(fresh.design_spec?.metrics)
      const metrics = currentMetrics.some((candidate) => candidate.id === metric.id)
        ? currentMetrics.map((candidate) => candidate.id === metric.id ? metric : candidate)
        : [...currentMetrics, metric]
      const measurementPlan = applyCustomMetricChange(fresh.measurement_plan, metric, config, graph)
      return experimentsApi.update(experimentId, {
        measurement_plan: measurementPlan,
        measurement_validation_protocol_id: protocolId,
        design_spec: { ...fresh.design_spec, metrics },
      })
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(['experiments', experimentId], updated)
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-impact'] })
    },
  })

  const close = useCallback(() => {
    flushAutosaveRef.current?.()
    onOpenChange(false)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={(next) => next ? onOpenChange(true) : close()}>
      <DialogContent className={NODE_INSPECTOR_CONTENT_CLASSNAME}>
        <DialogHeader className="shrink-0 border-b p-4 pr-12">
          <DialogTitle>{metricId ? 'Edit custom metric' : 'Create custom metric'}</DialogTitle>
          <DialogDescription>{metricId ? 'Update this reported metric and its source.' : 'Capture an Agent output or tool result for display and export.'}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {experimentQuery.isPending ? (
            <p className="text-sm text-muted-foreground">Loading experiment metrics…</p>
          ) : experimentQuery.isError ? (
            <p role="alert" className="text-sm text-destructive">Could not load this experiment's metrics.</p>
          ) : metricId && !existingMetric ? (
            <p role="alert" className="text-sm text-destructive">This custom metric no longer exists.</p>
          ) : (
            <CustomMetricFlow
              key={existingMetric?.id ?? `${sourceContext.producer}-${sourceContext.nodeId}`}
              metric={existingMetric ?? draft}
              binding={existingBinding}
              graph={graph}
              existingMetrics={existingMetrics}
              sourceContext={sourceContext}
              submitting={createMutation.isPending}
              autosave
              onAutosaveReady={(flush) => { flushAutosaveRef.current = flush }}
              onCancel={close}
              onDirtyChange={() => undefined}
              onSave={(metric, config) => createMutation.mutateAsync({ metric, config })}
            />
          )}
          {createMutation.isError && (
            <p role="alert" className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {createMutation.error instanceof Error ? createMutation.error.message : 'Could not create the custom metric.'}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
