import type { Connection, Edge, Node } from '@xyflow/react'
import { describe, expect, it } from 'vitest'
import { isProtocolConnectionValid } from './connectionValidation'

const target: Node = { id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: {} }

describe('isProtocolConnectionValid', () => {
  const cappedSlots = [
    ['model', 'model_openai'],
    ['memory', 'memory'],
    ['architectural_pattern', 'pattern_reason_act'],
    ['output_parser', 'output_parser'],
  ] as const

  it.each(cappedSlots)('rejects a second %s connection to the same node', (slot, sourceType) => {
    const firstSource: Node = { id: 'first', type: sourceType, position: { x: 0, y: 0 }, data: {} }
    const secondSource: Node = { id: 'second', type: sourceType, position: { x: 0, y: 0 }, data: {} }
    const existingEdge: Edge = {
      id: 'existing',
      source: firstSource.id,
      sourceHandle: slot,
      target: target.id,
      targetHandle: slot,
    }
    const connection: Connection = {
      source: secondSource.id,
      sourceHandle: slot,
      target: target.id,
      targetHandle: slot,
    }

    expect(isProtocolConnectionValid(connection, [target, firstSource, secondSource], [existingEdge], false)).toBe(false)
  })

  it.each(cappedSlots)('accepts the first %s connection', (slot, sourceType) => {
    const source: Node = { id: 'source', type: sourceType, position: { x: 0, y: 0 }, data: {} }
    const connection: Connection = {
      source: source.id,
      sourceHandle: slot,
      target: target.id,
      targetHandle: slot,
    }

    expect(isProtocolConnectionValid(connection, [target, source], [], false)).toBe(true)
  })

  it.each([
    ['tool', 'mcp_tool'],
    ['dataset', 'dataset'],
    ['skill', 'skill'],
    ['knowledge', 'okf_bundle'],
  ])('continues to accept multiple %s connections', (slot, sourceType) => {
    const firstSource: Node = { id: 'first', type: sourceType, position: { x: 0, y: 0 }, data: {} }
    const secondSource: Node = { id: 'second', type: sourceType, position: { x: 0, y: 0 }, data: {} }
    const existingEdge: Edge = {
      id: 'existing',
      source: firstSource.id,
      sourceHandle: slot,
      target: target.id,
      targetHandle: slot,
    }
    const connection: Connection = {
      source: secondSource.id,
      sourceHandle: slot,
      target: target.id,
      targetHandle: slot,
    }

    expect(isProtocolConnectionValid(connection, [target, firstSource, secondSource], [existingEdge], false)).toBe(true)
  })
})
