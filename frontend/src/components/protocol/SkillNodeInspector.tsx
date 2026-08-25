import { nodeAccent } from '@/lib/nodeAccent'
import { useQuery } from '@tanstack/react-query'
import { ScrollText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { skillsApi } from '@/api/client'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { ProtocolNode, SkillNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('skill')

// Same floating-dialog shell as McpToolNodeInspector, and the same model: no
// Skill field at all. Every Skill node is created by picking a skill in the
// Skills browser (SkillBrowserPanel/skillCatalog.ts), which pins
// skill_id/skill_name onto the node's data -- so a node IS one skill, and
// this inspector is purely "is it on, and what does it say". A dropdown here
// would be a second, contradicting place to answer a question the browser
// already answered; repointing a node at a different skill after the fact
// contradicts the one-node-per-skill model. Add a second Skill node instead.
//
// The document itself is read-only here. Editing a skill means editing the
// library row, and the same registered skill can be wired into many agents
// across many protocols, so an inline edit would silently change all of them.
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
  const skillsQuery = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list() })

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const selectedSkill = skillsQuery.data?.find((s) => s.id === config.skill_id)

  function patchConfig(patch: Partial<SkillNodeData['config']>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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

      <div className="space-y-1.5">
        <Label>Skill</Label>
        {skillsQuery.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : skillsQuery.isError ? (
          <p className="text-sm text-destructive">Could not load your registered skills.</p>
        ) : !selectedSkill ? (
          // The node names a skill GET /skills no longer returns (deleted from
          // the library, or a protocol JSON imported from another account).
          // Deliberately no dropdown to repoint it -- replacing the node from
          // the Skills panel is the fix, same as a deregistered MCP server.
          <p className="text-sm text-muted-foreground">
            <span className="font-mono">{config.skill_name ?? 'This node’s skill'}</span> isn&rsquo;t registered. Delete
            this node and add it again from the Skills panel.
          </p>
        ) : (
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
            {/* Level 3: named, not shown. These are read one at a time during a
                run, when the instructions send the agent to one -- so what
                matters here is that they came along with the upload, and a
                second full document inside this panel would bury the body. */}
            {selectedSkill.files.length > 0 && (
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">
                  Bundled files ({selectedSkill.files.length}, read on demand during a run)
                </p>
                <ul className="space-y-0.5">
                  {selectedSkill.files.map((path) => (
                    <li key={path} className="truncate font-mono text-[0.7rem] text-muted-foreground/70" title={path}>
                      {path}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">
                Instructions ({selectedSkill.body.length.toLocaleString()} characters, loaded only when the agent opens
                this skill)
              </p>
              {/* Sized off the viewport for the same reason McpToolNodeInspector's
                  tool list is: the inspector frame is already full-height, so the
                  document should use what's left rather than stopping at a fixed
                  12rem inside a mostly-empty dialog. */}
              <pre className="max-h-[calc(100vh-24rem)] min-h-32 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[0.7rem] whitespace-pre-wrap">
                {selectedSkill.body || '(empty)'}
              </pre>
            </div>
          </div>
        )}
      </div>
    </NodeInspectorDialog>
  )
}
