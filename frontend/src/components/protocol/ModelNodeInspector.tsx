import { useEffect, useState } from 'react'
import { Check, LoaderCircle, PlugZap, Sparkles, Split, Trash2 } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Button } from '@/components/ui/button'
import { ConnectionStatusBadge, useConnectionCheck } from '@/components/LlmConnectionCheck'
import { CreateCredentialDialog } from '@/components/CreateCredentialDialog'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { ModelField } from './ModelField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import { PROVIDER_META } from './nodes/ModelNode'
import { useProviderModels } from './useProviderModels'
import type { ModelNodeConfig, ModelNodeData, ProtocolNode } from '@/types/protocols'
import type { LLMProvider } from '@/types/llmSettings'

const EFFORT_LEVELS = ['low', 'medium', 'high', 'xhigh', 'max'] as const

// Shared by all three LLM provider node types (model_anthropic/model_openai/
// model_azure_foundry) -- fields are identical across providers (see
// ModelNodeData's own comment in types/protocols.ts), only the Credential
// section's content differs, branched on config.provider below. Model/
// Temperature/Effort/Max tokens are exactly the fields AgentNodeInspector/
// CriticGateNodeInspector used to have, relocated here -- this is now the
// ONLY place that config lives, resolved at execution time via the agent/
// critic_gate's required Model connector (services.protocol_execution's
// _resolve_model_config). The free-text "Provider" field is gone entirely --
// provider is fixed by which node type you picked from the "+" panel, not a
// field you fill in.
export function ModelNodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: ModelNodeData }) | null
  onChange: (nodeId: string, data: ModelNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  const [credentialDialogOpen, setCredentialDialogOpen] = useState(false)
  // Shown instead of closing outright when a required field (see ModelNode.tsx's
  // matching warning-triangle check) is still empty -- lets the user close
  // anyway rather than trapping them in the inspector, but makes sure they
  // saw it first. Same convention as ReasonActPatternNodeInspector.
  const [pendingCloseWarning, setPendingCloseWarning] = useState(false)
  // Every provider now has a real per-user credential (credential_resolver.py's
  // SUPPORTED_PROVIDERS) -- no more "informational, nothing to set up" branch.
  const provider = node?.data.config?.provider
  // GET /llm-settings/{provider}/models -- a live per-credential listing for
  // anthropic and azure_foundry, the static catalog for openai (which has no
  // capability endpoint) and for anyone with no credential saved yet. See
  // llm_model_discovery.py's docstring for what each provider actually
  // answers. Shared with the canvas node cards via useProviderModels, so
  // opening this inspector reads the list those cards already fetched rather
  // than issuing its own request.
  const { credentialsQuery, hasCredential, modelsQuery, models } = useProviderModels(provider)
  // Credential health belongs where you *pick* the credential, not only in a
  // settings screen -- this is the canvas-side entry point. Click-driven
  // rather than firing when the inspector opens: opening a node is a
  // navigation action, and a check per open would spend a rate-limited
  // request every time someone glances at a node.
  const credentialCheck = useConnectionCheck(provider as LLMProvider | undefined)
  const configForEffort = node?.data.config
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
  const ACCENT = nodeAccent('model')

  // Unrecognized model (list still loading, discovery failed, or a
  // hand-typed value not in the catalog) -- default to temperature-only,
  // the same safe fallback Motoro's own DEFAULT_CAPABILITIES uses.
  const showTemperature = selectedModelInfo?.supports_temperature ?? true

  // Deliberately off-catalog: a model id set here that the fetched list
  // doesn't contain, once there IS a list to compare against (an empty list
  // means "couldn't tell," which the `note` above explains instead).
  const isOffCatalogModel = !!config.model && models.length > 0 && !selectedModelInfo

  const missingFields: string[] = []
  if (!config.model) missingFields.push('Model')
  if (config.max_tokens == null) missingFields.push('Max tokens')
  // Only required when shown: a model that doesn't support temperature (see
  // showTemperature above) has no field to fill in, so it's not flagged.
  // For models that DO support it, leaving it unset would otherwise let
  // Motoro's own ModelConfig default (0.7) apply silently -- required so
  // that value is always an explicit choice, not an invisible fallback.
  if (showTemperature && config.temperature == null) missingFields.push('Temperature')

  function requestClose() {
    if (missingFields.length > 0) {
      setPendingCloseWarning(true)
      return
    }
    onClose()
  }

  function patchConfig(patch: Partial<ModelNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
        if (!open) requestClose()
      }}
      accent={ACCENT}
      title={
        <>
          <Icon className="size-5" style={{ color: ACCENT }} />
          <h2 className="text-lg font-semibold">{data.label || meta.label}</h2>
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={requestClose}
    >
      <div className="rounded-lg border px-3 py-2">
        <p className="text-xs text-muted-foreground">
          <span className="font-medium text-chart-2">Make factor</span> has moved to the node toolbar. Hover over the Model node and click the <Split className="inline size-3 align-text-bottom text-chart-2" aria-hidden="true" /> icon.
          {' '}<span className="ml-4 inline-flex flex-col gap-1 align-middle">
            <span className="text-[10px]">Toolbar preview</span>
            <span role="img" aria-label="Node toolbar preview: delete and Make factor (the branching icon on the right)" className="inline-flex items-center gap-3">
              <Trash2 className="size-3" /><Split className="size-3 text-chart-2" />
            </span>
          </span>
        </p>
      </div>
      {Object.entries(bindings).map(([path, name]) => <div key={path} className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs">
        <span className="font-mono">{path}: {name}</span>
        <Button variant="outline" size="sm" onClick={() => unbindFactor(path)}>Remove binding</Button>
      </div>)}
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="flex items-center gap-1.5">
            Credential
          </Label>
          {credentialsQuery.isLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : (
            <>
              <div className="flex items-center gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  className="min-w-0 flex-1 justify-between"
                  onClick={() => setCredentialDialogOpen(true)}
                >
                  {/* "saved", not "connected" -- a stored credential has
                      never been contacted, and claiming otherwise next to
                      a red "Failed" badge would contradict itself. The
                      badge below is the only thing that reports health. */}
                  <span className="truncate">
                    {hasCredential ? `${meta.label} credential saved` : 'Set up credential'}
                  </span>
                  {hasCredential && <Check className="size-4 shrink-0 text-muted-foreground" />}
                </Button>
                {hasCredential && (
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    aria-label={`Test the ${meta.label} connection`}
                    title="Test connection (free, no tokens)"
                    onClick={() => credentialCheck.mutate()}
                    disabled={credentialCheck.isPending}
                  >
                    {credentialCheck.isPending ? (
                      <LoaderCircle className="size-3.5 animate-spin" />
                    ) : (
                      <PlugZap className="size-3.5" />
                    )}
                  </Button>
                )}
              </div>
              {credentialCheck.data && (
                <>
                  <ConnectionStatusBadge status={credentialCheck.data.status} />
                  {credentialCheck.data.status !== 'ok' && (
                    <p className="line-clamp-3 text-xs text-muted-foreground">{credentialCheck.data.detail}</p>
                  )}
                </>
              )}
              {credentialCheck.isError && (
                <p className="text-xs text-destructive">Could not run the check.</p>
              )}
            </>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="llm-model" className="flex items-center gap-1.5">
            Model
          </Label>
          <ModelField
            id="llm-model"
            value={config.model}
            models={models}
            isLoading={modelsQuery.isLoading}
            onChange={(model) => patchConfig({ model })}
          />
          {modelsQuery.data?.source === 'error' && modelsQuery.data.note && (
            <p className="text-xs text-muted-foreground">{modelsQuery.data.note}</p>
          )}
          {isOffCatalogModel && (
            // Say why the controls just changed shape: capabilities are
            // looked up by model id, so an id the catalog doesn't know
            // falls back to DEFAULT_CAPABILITIES -- Temperature shown,
            // Effort hidden -- regardless of what the model really
            // supports. Worth stating plainly rather than letting the
            // Effort control silently vanish.
            <p className="text-xs text-muted-foreground">
              Not in the catalog, so its capabilities are unknown — Temperature is offered and Effort isn&apos;t. The
              id is sent to {meta.label} as typed.
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {showTemperature && (
          <div className="space-y-1.5">
            <Label htmlFor="llm-temperature" className="flex items-center gap-1.5">
              Temperature
            </Label>
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
        )}
        {showEffort && (
          <div className="space-y-1.5">
            <Label className="flex items-center gap-1.5">
              Effort
            </Label>
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
        )}
        <div className="space-y-1.5">
          <Label htmlFor="llm-max-tokens" className="flex items-center gap-1.5">
            Max tokens
          </Label>
          <Input
            id="llm-max-tokens"
            type="number"
            min="1"
            max="200000"
            value={config.max_tokens ?? ''}
            onChange={(e) => {
              if (e.target.value === '') {
                patchConfig({ max_tokens: null })
                return
              }
              patchConfig({ max_tokens: Math.min(200000, Math.max(1, Math.trunc(Number(e.target.value)))) })
            }}
          />
        </div>
      </div>
      <CreateCredentialDialog
        open={credentialDialogOpen}
        onOpenChange={setCredentialDialogOpen}
        defaultProvider={(provider as LLMProvider | undefined) ?? null}
      />

      <Dialog open={pendingCloseWarning} onOpenChange={(open) => !open && setPendingCloseWarning(false)}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Required fields are empty</DialogTitle>
            <DialogDescription>
              {missingFields.join(' and ')} {missingFields.length === 1 ? 'is' : 'are'} required for this connector to run. You can close and fill{' '}
              {missingFields.length === 1 ? 'it' : 'them'} in later, but the node will stay flagged until you do.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingCloseWarning(false)}>
              Go back
            </Button>
            <Button onClick={onClose}>Close anyway</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </NodeInspectorDialog>
  )
}
