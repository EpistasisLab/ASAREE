import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, ScrollText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { skillsApi } from '@/api/client'
import { hashToChartHue } from '@/lib/utils'
import type { Skill } from '@/types/skills'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { RegisterSkillDialog } from './RegisterSkillDialog'
import type { ProtocolNode, SkillNodeData } from '@/types/protocols'

const ACCENT = hashToChartHue('skill')

// Same floating-dialog shell as DatasetNodeInspector, and the same one real
// parameter: WHICH registered skill this node names, picked from the caller's
// own library (GET /skills), never hand-typed. A skill_id in an imported
// protocol JSON is per-account (same as a Dataset node's dataset_id or an MCP
// Tool node's server_id) -- an imported Skill node just shows "Select a
// skill…" until the importing user picks or uploads the real one.
//
// The body is shown read-only. Editing a skill means editing its document,
// which belongs in the skill library rather than in one protocol's node: the
// same registered skill can be wired into many agents across many protocols,
// so an inline edit here would silently change all of them.
export function SkillNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: SkillNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's agentTracedLabel)
  // -- distinct from data.label, this node's own plain header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: SkillNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const [registerDialogOpen, setRegisterDialogOpen] = useState(false)
  const skillsQuery = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedSkill = skillsQuery.data?.find((s) => s.id === config.skill_id)

  function patchConfig(patch: Partial<SkillNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  // name/description are cached onto the node purely so the card and this
  // header can read without a fetch -- the run always resolves the server's
  // own copy by id (see _resolve_skill_config).
  function selectSkill(skill: Skill) {
    patchConfig({ skill_id: skill.id, skill_name: skill.name, skill_description: skill.description })
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(node!.id, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(node!.id, { ...data, factor_bindings: next })
  }

  return (
    <NodeInspectorDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      accent={ACCENT}
      title={
        <>
          <ScrollText className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Skill" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.enabled"
        defaultLabel="Enabled"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.enabled']}
        onBind={(name) => bindFactor('config.enabled', name)}
        onUnbind={() => unbindFactor('config.enabled')}
      >
        {(trigger) => (
          <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor="skill-enabled" className="flex items-center gap-1.5">
                Enabled
                {trigger}
              </Label>
              <p className="text-xs text-muted-foreground">Off: the wired agent never sees this skill at all.</p>
            </div>
            <Switch id="skill-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
          </div>
        )}
      </FactorBindableField>

      {/* Uploading is deliberately OUTSIDE every branch below: registering a
          skill is a POST that doesn't care whether the list GET succeeded, and
          hiding the button on a failed/empty list leaves a fresh install with
          no way at all to add its first skill. */}
      <div className="space-y-1.5">
        <Label>Skill</Label>
        {skillsQuery.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : skillsQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your registered skills -- you can still register a new one.</p>
        ) : !skillsQuery.data || skillsQuery.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No skills registered yet.</p>
        ) : (
          <Select
            value={config.skill_id ?? '__none__'}
            onValueChange={(value) => {
              if (!value || value === '__none__') return
              const skill = skillsQuery.data.find((s) => s.id === value)
              if (skill) selectSkill(skill)
            }}
          >
            <SelectTrigger className="w-full">
              <SelectValue>{() => selectedSkill?.name ?? 'Select a skill…'}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__" disabled>
                Select a skill…
              </SelectItem>
              {skillsQuery.data.map((skill) => (
                <SelectItem key={skill.id} value={skill.id}>
                  {skill.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button variant="outline" size="sm" onClick={() => setRegisterDialogOpen(true)}>
          <Plus className="size-3.5" /> Register new skill
        </Button>
      </div>

      {selectedSkill && (
        <div className="space-y-2 rounded-lg border px-3 py-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate font-mono text-xs" title={selectedSkill.name}>
              {selectedSkill.name}
            </p>
            <div className="flex shrink-0 items-center gap-1.5">
              {selectedSkill.is_system && <Badge variant="outline">Built-in</Badge>}
              {selectedSkill.source_filename && (
                <Badge variant="outline" className="font-mono text-xs">
                  {selectedSkill.source_filename}
                </Badge>
              )}
            </div>
          </div>
          {/* The description is the only part the model always sees -- the
              body is loaded on demand, when the model decides this skill
              applies -- so it's shown first and in full, not truncated. */}
          <p className="text-xs text-muted-foreground">{selectedSkill.description}</p>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              Instructions ({selectedSkill.body.length.toLocaleString()} characters, loaded only when the agent opens this skill)
            </p>
            <pre className="max-h-48 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[0.7rem] whitespace-pre-wrap">
              {selectedSkill.body || '(empty)'}
            </pre>
          </div>
        </div>
      )}

      <RegisterSkillDialog open={registerDialogOpen} onOpenChange={setRegisterDialogOpen} onCreated={(skill) => selectSkill(skill)} />
    </NodeInspectorDialog>
  )
}
