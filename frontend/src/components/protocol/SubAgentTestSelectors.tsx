import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedSubAgents } from '@/lib/subAgentFactors'
import type { useSubAgentTestSelection } from './useSubAgentTestSelection'

export function SubAgentTestSelectors({ selection }: { selection: ReturnType<typeof useSubAgentTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedSubAgents(selection.graph!, node.id)
    const toggle = node.data.sub_agent_factor_mode === 'sub_agent_toggle'
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.label}: {toggle ? 'sub-agents' : 'sub-agent'} for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{toggle ? selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled' : String(options.find((option) => option.id === selection.selections[node.id])?.data.label ?? 'Choose a Sub-Agent…')}</SelectValue></SelectTrigger>
        <SelectContent>{toggle ? <><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></> : options.map((option) => <SelectItem key={option.id} value={option.id}>{String(option.data.label)}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
