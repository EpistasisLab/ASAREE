import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Square } from 'lucide-react'
import { protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import type { Protocol } from '@/types/protocols'

export function StopRowRuns({ protocol, runIds }: { protocol: Protocol; runIds: string[] }) {
  const client = useQueryClient()
  const stop = useMutation({
    mutationFn: async () => {
      const outcomes = await Promise.allSettled([...new Set(runIds)].map(id => protocolsApi.cancelRun(protocol.id, id)))
      if (outcomes.some(result => result.status === 'rejected')) throw new Error('Some runs could not be stopped. Try again.')
    },
    onSettled: () => client.invalidateQueries({ queryKey: ['experiments', protocol.experiment_id] }),
  })
  if (!runIds.length) return null
  return <div><Button size="xs" variant="outline" className="h-5 border-destructive/40 px-1.5 text-[0.65rem] text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={stop.isPending} onClick={() => stop.mutate()}><Square className="size-3" />{stop.isPending ? 'Stopping…' : runIds.length > 1 ? 'Stop all' : 'Stop'}</Button>{stop.error && <p role="alert" className="text-xs text-destructive">{stop.error.message}</p>}</div>
}
