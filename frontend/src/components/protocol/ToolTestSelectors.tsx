import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedTools, toolId, TOOL_FACTOR_PATH } from '@/lib/toolFactors'
import type { useToolTestSelection } from './useToolTestSelection'

export function ToolTestSelectors({ selection }: { selection: ReturnType<typeof useToolTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedTools(selection.graph!, node.id)
    if (node.data.tool_factor_mode === 'tool_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[TOOL_FACTOR_PATH] || node.data.label}: tools for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[TOOL_FACTOR_PATH] || node.data.label}: tool for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => toolId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a tool…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={toolId(option) || option.id} disabled={!selection.library.data?.some((tool) => tool.id === (option.data.config as { server_id?: string })?.server_id)}>{String(option.data.label)}{selection.library.isSuccess && !selection.library.data.some((tool) => tool.id === (option.data.config as { server_id?: string })?.server_id) ? ' (unavailable)' : ''}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
