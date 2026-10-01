import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { upsertAgentOutputMetric } from './agentOutputMetrics'
import { upsertMcpToolMetric, type McpToolSourceOption } from './mcpToolMetrics'
import type { FieldProjection } from './metricFields'
import { removeMetricFromMeasurementPlan } from './measurementPlan'
import { pythonScriptSourceOptions, upsertPythonScriptMetric } from './pythonScriptMetrics'
import { upsertToolStepMetric } from './toolStepMetrics'

export type CustomMetricProducer = 'agent' | 'python' | 'mcp' | 'tool_step'

export type CustomMetricSourceContext =
  | { producer: 'agent'; nodeId: string }
  | { producer: 'python'; nodeId: string }
  | { producer: 'mcp'; nodeId: string }
  | { producer: 'tool_step'; nodeId: string }

// `projection` picks one field of the output; absent records the whole of it.
export type CustomMetricProducerConfig =
  | { producer: 'agent'; agentNodeId: string; projection?: FieldProjection }
  | { producer: 'tool_step'; nodeId: string; projection?: FieldProjection }
  | { producer: 'python'; sourceKey: string }
  | { producer: 'mcp'; source: McpToolSourceOption; toolName: string }

// The one write interface shared by Manage Metrics and the canvas's
// node-first custom-metric flow. Keeping producer replacement here prevents
// the two entry points from drifting on binding ids or source configuration.
export function applyCustomMetricChange(
  plan: MeasurementPlan | null,
  metric: DesignMetric,
  config: CustomMetricProducerConfig,
  graph: ProtocolGraph | undefined,
): MeasurementPlan {
  const planWithoutPreviousProducer = removeMetricFromMeasurementPlan(plan, metric.id)
  if (config.producer === 'agent') {
    return upsertAgentOutputMetric(planWithoutPreviousProducer, metric, config.agentNodeId, config.projection)
  }
  if (config.producer === 'tool_step') {
    return upsertToolStepMetric(planWithoutPreviousProducer, metric, config.nodeId, config.projection)
  }
  if (config.producer === 'mcp') {
    return upsertMcpToolMetric(
      planWithoutPreviousProducer,
      metric,
      config.source,
      config.toolName,
    )
  }
  const source = pythonScriptSourceOptions(graph).find((option) => option.key === config.sourceKey)
  return upsertPythonScriptMetric(planWithoutPreviousProducer, metric, source)
}
