import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Sparkles } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { CreateCredentialDialog } from '@/components/CreateCredentialDialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { hashToChartHue } from '@/lib/utils'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PROVIDER_META } from './nodes/LlmNode'
import type { LlmNodeConfig, LlmNodeData, ProtocolNode } from '@/types/protocols'
import type { LLMProvider } from '@/types/llmSettings'

const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const

// Shared by all three LLM provider node types (llm_anthropic/llm_openai/
// llm_azure_foundry) -- fields are identical across providers (see
// LlmNodeData's own comment in types/protocols.ts), only the Credential
// section's content differs, branched on config.provider below. Model/
// Temperature/Effort/Max tokens are exactly the fields AgentNodeInspector/
// CriticGateNodeInspector used to have, relocated here -- this is now the
// ONLY place that config lives, resolved at execution time via the agent/
// critic_gate's required LLM connector (services.protocol_execution's
// _resolve_llm_config). The free-text "Provider" field is gone entirely --
// n8n-style, provider is fixed by which node type you picked from the "+"
// panel, not a field you fill in.
export function LlmNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: LlmNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's
  // agentTracedLabel) -- distinct from data.label/meta.label, which is this
  // node's own plain label/provider name shown in the header title. Two
  // different agents' LLM nodes can share the exact same plain label (e.g.
  // both "Anthropic"), so factor names need this instead to stay
  // unambiguous.
  factorNodeLabel: string
  onChange: (nodeId: string, data: LlmNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  // Every provider now has a real per-user credential (credential_resolver.py's
  // SUPPORTED_PROVIDERS) -- no more "informational, nothing to set up" branch.
  const provider = node?.data.config?.provider
  const credentialsQuery = useQuery({
    queryKey: ['llm-settings'],
    queryFn: () => llmSettingsApi.list(),
    enabled: !!provider,
  })
  const hasCredential = (credentialsQuery.data ?? []).some((c) => c.provider === provider)
  // GET /llm-settings/{provider}/models -- static, credential-free catalog
  // for anthropic/openai; a live Azure Foundry deployment discovery once a
  // credential exists (see llm_model_discovery.py for why Azure can't use a
  // static catalog the way the other two do). Not fetched for Azure until a
  // credential exists -- nothing to discover without one.
  const modelsQuery = useQuery({
    queryKey: ['llm-settings', provider, 'models'],
    queryFn: () => llmSettingsApi.listModels(provider!),
    enabled: !!provider && (provider !== 'azure_foundry' || hasCredential),
  })

  const configForEffort = node?.data.config
  const models = modelsQuery.data?.models ?? []
  const selectedModelInfo = models.find((m) => m.id === configForEffort?.model)
  const showEffort = selectedModelInfo?.supports_effort ?? false
  const effortLevels = selectedModelInfo?.effort_levels.length ? selectedModelInfo.effort_levels : EFFORT_LEVELS

  // A model that supports effort defaults to something usable instead of
  // "(none)" -- "medium" when the model offers it (a reasonable middle
  // ground of quality vs. cost), otherwise whatever its own list's first
  // entry is. Only fires when effort is genuinely unset AND the currently
  // selected model actually supports it -- switching to a model that
  // doesn't support effort leaves config.effort alone (it's just not shown
  // or used), rather than clearing a value the user may switch back to.
  // Runs before the `!node` early return below (hooks can't be conditional).
  useEffect(() => {
    if (node && showEffort && !configForEffort?.effort && effortLevels.length > 0) {
      onChange(node.id, {
        ...node.data,
        config: { ...node.data.config, effort: effortLevels.includes('medium') ? 'medium' : effortLevels[0] },
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node, showEffort, configForEffort?.effort, effortLevels])

  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  const meta = PROVIDER_META[provider!] ?? { label: provider, icon: Sparkles }
  const Icon = meta.icon
  const ACCENT = hashToChartHue(provider || 'llm')

  // Unrecognized model (list still loading, discovery failed, or a
  // hand-typed value not in the catalog) -- default to temperature-only,
  // the same safe fallback agentic-core's own DEFAULT_CAPABILITIES uses.
  const showTemperature = selectedModelInfo?.supports_temperature ?? true

  function patchConfig(patch: Partial<LlmNodeConfig>) {
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
          <Icon className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || meta.label}</h2>
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="grid grid-cols-2 gap-4">
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config"
          defaultLabel="Provider & model"
          nodeLabel={factorNodeLabel}
          levelType="llm_config"
          currentValue={config}
          boundFactorName={bindings.config}
          onBind={(name) => bindFactor('config', name)}
          onUnbind={() => unbindFactor('config')}
        >
          <div className="space-y-1.5">
            <Label>Credential</Label>
            {credentialsQuery.isLoading ? (
              <Skeleton className="h-8 w-full" />
            ) : (
              <Button
                type="button"
                variant="outline"
                className="w-full justify-between"
                onClick={() => setCredentialDialogOpen(true)}
              >
                <span>{hasCredential ? `${meta.label} credential connected` : 'Set up credential'}</span>
                {hasCredential && <Check className="size-4 text-[color:var(--chart-3)]" />}
              </Button>
            )}
          </div>
        </FactorBindableField>
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.model"
          defaultLabel="Model"
          nodeLabel={factorNodeLabel}
          levelType="string"
          currentValue={config.model}
          levelOptions={models.length > 0 ? models.map((m) => ({ value: m.id, label: m.label ?? m.id })) : undefined}
          boundFactorName={bindings['config.model']}
          onBind={(name) => bindFactor('config.model', name)}
          onUnbind={() => unbindFactor('config.model')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="llm-model">Model</Label>
            {modelsQuery.isLoading ? (
              <Skeleton className="h-8 w-full" />
            ) : models.length > 0 ? (
              <Select
                value={selectedModelInfo?.id ?? '__none__'}
                onValueChange={(value) => {
                  if (value && value !== '__none__') patchConfig({ model: value })
                }}
              >
                <SelectTrigger id="llm-model" className="w-full">
                  <SelectValue>{() => selectedModelInfo?.label ?? selectedModelInfo?.id ?? 'Select a model…'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__" disabled>
                    Select a model…
                  </SelectItem>
                  {models.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {m.label ?? m.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              // Falls back to free-text when there's nothing to list yet --
              // no Azure credential, or live discovery failed (see the note
              // below) -- never a dead end.
              <Input id="llm-model" value={config.model} onChange={(e) => patchConfig({ model: e.target.value })} />
            )}
            {modelsQuery.data?.source === 'error' && modelsQuery.data.note && (
              <p className="text-xs text-muted-foreground">{modelsQuery.data.note}</p>
            )}
          </div>
        </FactorBindableField>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {showTemperature && (
          <FactorBindableField
            experimentId={experimentId}
            fieldPath="config.temperature"
            defaultLabel="Temperature"
            nodeLabel={factorNodeLabel}
            levelType="number"
            currentValue={config.temperature}
            boundFactorName={bindings['config.temperature']}
            onBind={(name) => bindFactor('config.temperature', name)}
            onUnbind={() => unbindFactor('config.temperature')}
          >
            <div className="space-y-1.5">
              <Label htmlFor="llm-temperature">Temperature</Label>
              <Input
                id="llm-temperature"
                type="number"
                step="0.1"
                min="0"
                max="2"
                value={config.temperature ?? ''}
                onChange={(e) => patchConfig({ temperature: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </div>
          </FactorBindableField>
        )}
        {showEffort && (
          <FactorBindableField
            experimentId={experimentId}
            fieldPath="config.effort"
            defaultLabel="Effort"
            nodeLabel={factorNodeLabel}
            levelType="string"
            currentValue={config.effort}
            levelOptions={effortLevels.map((level) => ({ value: level, label: level }))}
            boundFactorName={bindings['config.effort']}
            onBind={(name) => bindFactor('config.effort', name)}
            onUnbind={() => unbindFactor('config.effort')}
          >
            <div className="space-y-1.5">
              <Label>Effort</Label>
              <Select
                value={config.effort ?? '__none__'}
                onValueChange={(value) => {
                  if (value === null) return
                  patchConfig({ effort: value === '__none__' ? null : value })
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>{(value: string) => (value === '__none__' ? '(none)' : value)}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">(none)</SelectItem>
                  {effortLevels.map((level) => (
                    <SelectItem key={level} value={level}>
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </FactorBindableField>
        )}
        <FactorBindableField
          experimentId={experimentId}
          fieldPath="config.max_tokens"
          defaultLabel="Max tokens"
          nodeLabel={factorNodeLabel}
          levelType="number"
          currentValue={config.max_tokens}
          boundFactorName={bindings['config.max_tokens']}
          onBind={(name) => bindFactor('config.max_tokens', name)}
          onUnbind={() => unbindFactor('config.max_tokens')}
        >
          <div className="space-y-1.5">
            <Label htmlFor="llm-max-tokens">Max tokens</Label>
            <Input
              id="llm-max-tokens"
              type="number"
              min="1"
              value={config.max_tokens}
              onChange={(e) => patchConfig({ max_tokens: Number(e.target.value) })}
            />
          </div>
        </FactorBindableField>
      </div>
      <CreateCredentialDialog
        open={credentialDialogOpen}
        onOpenChange={setCredentialDialogOpen}
        defaultProvider={(provider as LLMProvider | undefined) ?? null}
      />
    </NodeInspectorDialog>
  )
}
