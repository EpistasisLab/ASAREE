import type { DesignMetric, MeasurementPlan } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { upsertAgentOutputMetric } from './agentOutputMetrics'
import { upsertMcpToolMetric, type McpToolSourceOption } from './mcpToolMetrics'
import { removeMetricFromMeasurementPlan } from './measurementPlan'
import { pythonScriptSourceOptions, upsertPythonScriptMetric } from './pythonScriptMetrics'

export type CustomMetricProducer = 'agent' | 'python' | 'mcp'

export type CustomMetricSourceContext =
  | { producer: 'agent'; nodeId: string }
  | { producer: 'python'; nodeId: string }
  | { producer: 'mcp'; nodeId: string }

export type CustomMetricProducerConfig =
  | { producer: 'agent'; agentNodeId: string }
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
    return upsertAgentOutputMetric(planWithoutPreviousProducer, metric, config.agentNodeId)
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
