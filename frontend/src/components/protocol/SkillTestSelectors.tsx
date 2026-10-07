import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedSkills, skillId } from '@/lib/skillFactors'
import type { useSkillTestSelection } from './useSkillTestSelection'

export function SkillTestSelectors({ selection }: { selection: ReturnType<typeof useSkillTestSelection> }) {
  return <div className="space-y-2">{selection.owners.map((node) => {
    const options = connectedSkills(selection.graph!, node.id)
    if (node.data.skill_factor_mode === 'skill_toggle') return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.label}: skills for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(value) => value && selection.setChoice(node.id, value)}>
        <SelectTrigger className="w-full"><SelectValue>{selection.selections[node.id] === 'all' ? 'All enabled' : 'All disabled'}</SelectValue></SelectTrigger>
        <SelectContent><SelectItem value="all">All enabled</SelectItem><SelectItem value="none">All disabled</SelectItem></SelectContent>
      </Select>
    </div>
    return <div key={node.id} className="space-y-1">
      <p className="text-xs">{node.data.label}: skill for this test</p>
      <Select value={selection.selections[node.id]} onValueChange={(id) => id && selection.setChoice(node.id, id)}>
        <SelectTrigger className="w-full"><SelectValue>{() => {
          const selected = options.find((option) => skillId(option) === selection.selections[node.id])
          return String(selected?.data.label ?? 'Choose a skill…')
        }}</SelectValue></SelectTrigger>
        <SelectContent>{options.map((option) => <SelectItem key={option.id} value={skillId(option) || option.id} disabled={!selection.library.data?.some((skill) => skill.id === skillId(option))}>{String(option.data.label)}{selection.library.isSuccess && !selection.library.data.some((skill) => skill.id === skillId(option)) ? ' (unavailable)' : ''}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  })}{selection.error && <p role="alert" className="text-xs text-destructive">{selection.error}</p>}</div>
}
