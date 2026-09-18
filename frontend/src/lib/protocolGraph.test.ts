import { describe, expect, it } from 'vitest'
import { mergeProtocolSaveIntoCache } from './protocolGraph'
import { defaultAgentNodeData, type Protocol } from '@/types/protocols'

function protocol(revision: number, graphLabel: string): Protocol {
  return {
    id: 'protocol-1',
    name: 'Protocol',
    description: null,
    experiment_id: 'experiment-1',
    graph: { nodes: [{ id: 'agent-1', type: 'agent', position: { x: 0, y: 0 }, data: defaultAgentNodeData(graphLabel) }], edges: [] },
    published_revision_id: `revision-${revision}`,
    published_revision: revision,
    has_unpublished_changes: false,
    created_at: '',
    updated_at: '',
  }
}

describe('mergeProtocolSaveIntoCache', () => {
  it('does not let a late autosave response downgrade the published canvas version', () => {
    const published = protocol(2, 'new canvas')
    const lateAutosave = { ...protocol(1, 'new canvas'), has_unpublished_changes: true }

    expect(mergeProtocolSaveIntoCache(published, lateAutosave)).toMatchObject({
      published_revision_id: 'revision-2',
      published_revision: 2,
      has_unpublished_changes: false,
    })
  })
})
