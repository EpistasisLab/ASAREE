import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { experimentsApi, type ResultsScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { executionKeys } from '@/lib/executionQueries'

export interface ExecutionContext { experimentId: string; runId: string; scope: ResultsScope }

export function ExecutionNodeText({ context, nodeId, field, stepId }: { context: ExecutionContext; nodeId: string; field: 'prompt' | 'output'; stepId?: string }) {
  const [offsets, setOffsets] = useState([0])
  const offset = offsets.at(-1) ?? 0
  const query = useQuery({ queryKey: [...executionKeys.root(context.experimentId), 'node-text', context.scope, context.runId, nodeId, field, stepId, offset],
    queryFn: ({ signal }) => stepId
      ? experimentsApi.getExecutionStep(context.experimentId, context.runId, nodeId, stepId, context.scope, offset, signal)
      : experimentsApi.getExecutionNodeText(context.experimentId, context.runId, nodeId, context.scope, field, offset, signal) })
  return <section className="space-y-2"><h4 className="text-xs font-medium">{stepId ? 'Step evidence' : field === 'prompt' ? 'Received prompt' : 'Output'}</h4>
    {query.error ? <p role="alert" className="text-xs text-destructive">{query.error.message}</p> : <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border p-2 font-mono text-xs">{query.data?.text ?? 'Loading…'}</pre>}
    <div className="flex gap-2"><Button size="xs" variant="outline" disabled={offsets.length === 1 || query.isFetching} onClick={() => setOffsets(current => current.slice(0,-1))}>Previous text</Button><Button size="xs" variant="outline" disabled={query.data?.next_offset == null || query.isFetching} onClick={() => query.data?.next_offset != null && setOffsets(current => [...current, query.data!.next_offset!])}>Next text</Button></div>
  </section>
}

export function ExecutionNodeTrace({ context, nodeId }: { context: ExecutionContext; nodeId: string }) {
  const [open, setOpen] = useState(false)
  const [afters, setAfters] = useState([-1])
  const [selected, setSelected] = useState<string>()
  const after = afters.at(-1) ?? -1
  const query = useQuery({ queryKey: [...executionKeys.root(context.experimentId), 'trace', context.scope, context.runId, nodeId, after],
    queryFn: ({ signal }) => experimentsApi.getExecutionTrace(context.experimentId, context.runId, nodeId, context.scope, after, signal), enabled: open })
  return <section className="space-y-2"><Button variant="outline" size="xs" onClick={() => setOpen(value => !value)}>{open ? 'Hide trace' : 'View trace'}</Button>
    {open && <>{query.error && <p role="alert" className="text-xs text-destructive">{query.error.message}</p>}
      {query.data?.items.map(step => <div key={step.id} className="rounded border p-2 text-xs"><Button size="xs" variant="ghost" onClick={() => setSelected(current => current === step.id ? undefined : step.id)}>Step {step.sequence} · {step.phase} · iteration {step.iteration ?? '—'}</Button><p className="truncate font-mono">{step.preview}</p>{selected === step.id && <ExecutionNodeText key={step.id} context={context} nodeId={nodeId} field="output" stepId={step.id} />}</div>)}
      <div className="flex gap-2"><Button variant="outline" size="xs" disabled={afters.length === 1 || query.isFetching} onClick={() => { setAfters(current => current.slice(0,-1)); setSelected(undefined) }}>Previous steps</Button><Button variant="outline" size="xs" disabled={query.data?.next_after == null || query.isFetching} onClick={() => { if (query.data?.next_after != null) setAfters(current => [...current, query.data!.next_after!]); setSelected(undefined) }}>Next steps</Button></div>
    </>}
  </section>
}
