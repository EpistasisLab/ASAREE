import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { TestRun } from '@/types/protocols'
import { ReopenTestRunResultsButton, TestRunResults } from './TestRunResults'

const producer = { binding_id: 'runtime', producer_id: 'asaree.runtime', kind: 'runtime' as const, version: '1' }

function testRun(overrides: Partial<TestRun> = {}): TestRun {
  return {
    id: 'run-1',
    protocol_id: 'protocol-1',
    status: 'running',
    error: null,
    protocol_revision_id: 'revision-1',
    created_at: '2026-09-16T12:00:00Z',
    updated_at: '2026-09-16T12:00:01Z',
    observations: [],
    artifacts: [],
    conversation: null,
    tested_published_revision: { id: 'revision-1', number: 7, published_at: '2026-09-16T11:59:00Z' },
    freshness: { out_of_date: false, reasons: [] },
    resources: {
      task: { duration_seconds: null, cost_usd: null },
      evaluation: { duration_seconds: null, cost_usd: null },
      total: { duration_seconds: null, cost_usd: null },
    },
    execution_summary: {
      node_runs: { 'agent-1': { status: 'running', output_text: null } },
      started_at: '2026-09-16T12:00:00Z',
      completed_at: null,
      cancel_requested_at: null,
    },
    ...overrides,
  }
}

describe('TestRunResults', () => {
  it.each([
    ['completed', 'Completed'],
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled'],
    ['limit_reached', 'Limit reached'],
  ] as const)('presents the %s terminal outcome in place', (status, label) => {
    render(<TestRunResults run={testRun({ status })} onClose={vi.fn()} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('shows live node and conversation progress in the Test Run workspace', () => {
    render(<TestRunResults run={testRun({
      conversation: {
        state: 'working', entry_agent_id: 'agent-1',
        messages: [{ message_id: 'message-1', sequence: 1, from_agent_id: 'user', to_agent_id: 'agent-1', parts: [{ kind: 'text', text: 'Analyze this.' }], created_at: '2026-09-16T12:00:00Z' }],
      },
    })} agentNames={new Map([['agent-1', 'Analyst']])} onClose={vi.fn()} />)

    expect(screen.getByText('Task execution in progress…')).toBeInTheDocument()
    expect(screen.getByText('Analyst')).toBeInTheDocument()
    expect(screen.getByText('Agent conversation flow')).toBeInTheDocument()
    expect(screen.getByText('Analyze this.')).toBeInTheDocument()
  })

  it('shows a captured metric alongside downstream task progress', () => {
    render(<TestRunResults run={testRun({
      observations: [
        { metric_id: 'quality', metric_name: 'Quality', value_type: 'number', status: 'measured', value: 0.42, error: null, attempt_id: 'run-1', producer: { ...producer, binding_id: 'quality-from-agent-b' }, input_provenance: {} },
      ],
      execution_summary: {
        node_runs: {
          'agent-b': { status: 'completed', output_text: 'Intermediate answer' },
          'agent-c': { status: 'running', output_text: null },
        },
        started_at: '2026-09-16T12:00:00Z', completed_at: null, cancel_requested_at: null,
      },
    })} onClose={vi.fn()} />)

    expect(screen.getByText('Task execution in progress…')).toBeInTheDocument()
    expect(screen.getByText('Metric observations')).toBeInTheDocument()
    expect(screen.getByText('Quality')).toBeInTheDocument()
    expect(screen.getByText('0.42')).toBeInTheDocument()
  })

  it('shows an opaque reported tool result without scalar formatting', () => {
    render(<TestRunResults run={testRun({
      status: 'completed',
      observations: [
        {
          metric_id: 'report', metric_name: 'Reviewer report', value_type: 'number', status: 'measured',
          value: '{"labels":["safe","complete"]}', error: null, attempt_id: 'run-1',
          producer: { ...producer, binding_id: 'reported-tool' }, input_provenance: {},
        },
      ],
    })} onClose={vi.fn()} />)

    expect(screen.getByText('{"labels":["safe","complete"]}')).toBeInTheDocument()
  })

  it('transitions in place from metric calculation to a partial result with inspectable evidence', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<TestRunResults run={testRun({ status: 'finalizing' })} onClose={vi.fn()} />)
    expect(screen.getByText('Calculating metrics…')).toBeInTheDocument()

    rerender(<TestRunResults run={testRun({
      status: 'completed',
      observations: [
        { metric_id: 'cost', metric_name: 'Cost', value_type: 'number', status: 'measured', value: 1.25, error: null, attempt_id: 'run-1', producer, input_provenance: {} },
        { metric_id: 'quality', metric_name: 'Quality', value_type: 'number', status: 'failed', value: null, error: 'Evaluator crashed.', attempt_id: 'run-1', producer: { ...producer, binding_id: 'quality-tool', binding_config: { tool_name: 'score' } }, input_provenance: {} },
        { metric_id: 'slow', metric_name: 'Slow metric', value_type: 'number', status: 'timed_out', value: null, error: 'Administrative timeout.', attempt_id: 'run-1', producer, input_provenance: {} },
        { metric_id: 'stopped', metric_name: 'Stopped metric', value_type: 'number', status: 'cancelled', value: null, error: 'Evaluation was cancelled.', attempt_id: 'run-1', producer, input_provenance: {} },
      ],
      artifacts: [{ artifact_key: 'details', kind: 'diagnostics', payload: { rows: 12 }, attempt_id: 'run-1', producer: { ...producer, binding_id: 'quality-tool' }, input_provenance: {} }],
      execution_summary: { node_runs: { 'agent-1': { status: 'completed', output_text: 'Final answer' } }, started_at: '2026-09-16T12:00:00Z', completed_at: '2026-09-16T12:00:08Z', cancel_requested_at: null },
      resources: {
        task: { duration_seconds: 8, cost_usd: 1.25 },
        evaluation: { duration_seconds: 2, cost_usd: null },
        total: { duration_seconds: 10, cost_usd: null },
      },
    })} agentNames={new Map([['agent-1', 'Analyst']])} onClose={vi.fn()} />)

    expect(screen.getByText('Partially evaluated')).toBeInTheDocument()
    expect(screen.getByText('Task')).toBeInTheDocument()
    expect(screen.getByText('Evaluation')).toBeInTheDocument()
    expect(screen.getByText('Combined')).toBeInTheDocument()
    expect(screen.getByText('8.0 sec')).toBeInTheDocument()
    expect(screen.getByText('2.0 sec')).toBeInTheDocument()
    expect(screen.getByText('10 sec')).toBeInTheDocument()
    expect(screen.getByText('$1.25')).toBeInTheDocument()
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0)
    expect(screen.getByText('Evaluator crashed.')).toBeInTheDocument()
    expect(screen.getAllByText('Timed out').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0)
    await user.click(screen.getByText('Analyst'))
    expect(screen.getByText('Final answer')).toBeInTheDocument()
    await user.click(screen.getAllByText('Provenance and diagnostics')[1])
    expect(screen.getByText('1 linked evaluation artifact shown below.')).toBeInTheDocument()
    await user.click(screen.getByText('Evaluation artifacts'))
    expect(screen.getByText(/producer quality-tool/)).toBeInTheDocument()
    expect(screen.getByText(/"rows": 12/)).toBeInTheDocument()
  })

  it('updates retained evidence when a later poll detects relevant edits', () => {
    const { rerender } = render(<TestRunResults run={testRun({ status: 'cancelled' })} onClose={vi.fn()} />)
    expect(screen.queryByText('Out of date')).not.toBeInTheDocument()

    rerender(<TestRunResults run={testRun({
      status: 'cancelled',
      freshness: { out_of_date: true, reasons: ['canvas', 'measurement_plan'] },
    })} onClose={vi.fn()} />)

    expect(screen.getByText('Cancelled')).toBeInTheDocument()
    expect(screen.getAllByText('Out of date')).toHaveLength(2)
    expect(screen.getByText(/canvas and measurement plan have changed/i)).toBeInTheDocument()
    expect(screen.getByText(/Published revision 7/)).toBeInTheDocument()
  })

  it('closes its window without changing the active Test Run', async () => {
    const close = vi.fn()
    const refresh = vi.fn()
    const user = userEvent.setup()
    function Workspace() {
      const [open, setOpen] = useState(true)
      return open
        ? <TestRunResults run={testRun()} onClose={() => { close(); setOpen(false) }} />
        : <ReopenTestRunResultsButton onOpen={() => setOpen(true)} refresh={refresh} />
    }
    render(<Workspace />)

    await user.click(screen.getByRole('button', { name: 'Close Test Run Results' }))
    expect(close).toHaveBeenCalledOnce()
    expect(screen.queryByLabelText('Test Run Results')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Test Run Results' }))
    expect(refresh).toHaveBeenCalledOnce()
    expect(screen.getByLabelText('Test Run Results')).toBeInTheDocument()
    expect(screen.getByText('Task execution in progress…')).toBeInTheDocument()
  })
})
