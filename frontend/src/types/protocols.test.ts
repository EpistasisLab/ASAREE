import { describe, expect, it } from 'vitest'
import {
  defaultAnthropicModelNodeData,
  defaultAzureFoundryModelNodeData,
  defaultLocalModelNodeData,
  defaultOpenAiModelNodeData,
  defaultOpenRouterModelNodeData,
} from './protocols'

describe('Model node defaults', () => {
  it('requires an explicit model selection for every provider', () => {
    const defaults = [
      defaultAnthropicModelNodeData(),
      defaultOpenAiModelNodeData(),
      defaultAzureFoundryModelNodeData(),
      defaultOpenRouterModelNodeData(),
      defaultLocalModelNodeData(),
    ]

    expect(defaults.map(({ config }) => config.model)).toEqual(['', '', '', '', ''])
  })
})
