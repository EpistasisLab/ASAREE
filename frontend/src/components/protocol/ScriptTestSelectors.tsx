import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedScripts, scriptId, SCRIPT_FACTOR_PATH } from '@/lib/scriptFactors'
import type { useScriptTestSelection } from './useScriptTestSelection'

export function ScriptTestSelectors({ selection }: { selection: ReturnType<typeof useScriptTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedScripts(selection.graph!, node.id)
    if (node.data.script_factor_mode === 'script_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[SCRIPT_FACTOR_PATH] || node.data.label}: scripts for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[SCRIPT_FACTOR_PATH] || node.data.label}: script for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => scriptId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a script…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={option.id} disabled={!(option.data.config as { code?: string })?.code?.trim()}>{String(option.data.label)}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
