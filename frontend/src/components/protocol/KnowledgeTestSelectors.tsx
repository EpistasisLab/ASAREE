import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedKnowledge, knowledgeId, KNOWLEDGE_FACTOR_PATH } from '@/lib/knowledgeFactors'
import type { useKnowledgeTestSelection } from './useKnowledgeTestSelection'

export function KnowledgeTestSelectors({ selection }: { selection: ReturnType<typeof useKnowledgeTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedKnowledge(selection.graph!, node.id)
    if (node.data.knowledge_factor_mode === 'knowledge_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH] || node.data.label}: knowledge for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH] || node.data.label}: knowledge for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => knowledgeId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a knowledge…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={knowledgeId(option) || option.id} disabled={!selection.library.data?.some((knowledge) => knowledge.id === knowledgeId(option))}>{String(option.data.label)}{selection.library.isSuccess && !selection.library.data.some((knowledge) => knowledge.id === knowledgeId(option)) ? ' (unavailable)' : ''}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
