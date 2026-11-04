import { describe, expect, it } from 'vitest'
import { METRIC_CATALOG, makeCatalogMetric } from './metricCatalog'
import { upsertRuntimeMetric } from './measurementPlan'

describe('built-in measurement-plan definitions', () => {
  it.each(METRIC_CATALOG)('preserves the selected $name semantics in its plan', (entry) => {
    const selected = makeCatalogMetric(entry, false)
    const plan = upsertRuntimeMetric(null, selected)
    const declared = plan.metrics.find((metric) => metric.id === selected.id)!
    // The API rejects a selected metric when any of these semantics differ.
    expect({
      name: declared.name,
      valueType: declared.value_type,
      direction: declared.direction,
      aggregation: declared.aggregation,
    }).toEqual({
      name: selected.name,
      valueType: selected.valueType,
      direction: selected.direction,
      aggregation: selected.aggregation,
    })
  })
})
