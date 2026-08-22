import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, LoaderCircle, X } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { ConnectionStatusBadge, useConnectionCheck } from '@/components/LlmConnectionCheck'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { PasswordInput } from '@/components/ui/password-input'
import { cn, HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import { LLM_PROVIDER_CATALOG, type LLMProvider } from '@/types/llmSettings'
import { PROVIDER_META } from '@/components/protocol/nodes/LlmNode'

const PROVIDER_CATALOG = LLM_PROVIDER_CATALOG

export function CreateCredentialDialog({
  open,
  onOpenChange,
  defaultProvider = null,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  // Opening this from a specific LLM node's inspector should land straight
  // on that provider's fields, not the search screen -- only meaningful with
  // more than one catalog entry to search through.
  defaultProvider?: LLMProvider | null
}) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState<LLMProvider | null>(defaultProvider)
  const [apiKey, setApiKey] = useState('')
  const [azureProjectEndpoint, setAzureProjectEndpoint] = useState('')
  const [apiBase, setApiBase] = useState('')
  const queryClient = useQueryClient()

  const settingsQuery = useQuery({
    queryKey: ['llm-settings'],
    queryFn: () => llmSettingsApi.list(),
    enabled: open,
  })
  const existing = settingsQuery.data?.find((s) => s.provider === provider)
  // azure_foundry's Project endpoint is the only field asked for -- the
  // resource host api_base needs for inference is derived from it
  // server-side (upsert_setting), since it's already a prefix of that same
  // URL. Asking for both looked like two near-identical URLs with no
  // visible reason to differ.
  const requiresProjectEndpoint = provider === 'azure_foundry'
  // The only provider here with no default host to fall back to -- every
  // other provider either has a well-known API base (anthropic/openai/
  // openrouter) or derives one server-side (azure_foundry, from the Project
  // endpoint above).
  const requiresApiBase = provider === 'local'
  // A self-hosted server rarely checks the key -- api_key stays optional
  // for this provider only (see credential_resolver.py's "not-needed"
  // placeholder).
  const apiKeyOptional = provider === 'local'

  // Selecting a provider (either directly from the search list, or via
  // defaultProvider when opened from an existing credential's own "Edit")
  // pre-fills its non-secret fields from any already-saved setting -- the
  // API key stays blank either way, since it's write-only and never
  // echoed back by the API.
  function selectProvider(id: LLMProvider | null) {
    setProvider(id)
    const existingSetting = id ? settingsQuery.data?.find((s) => s.provider === id) : undefined
    setAzureProjectEndpoint(existingSetting?.azure_project_endpoint ?? '')
    setApiBase(existingSetting?.api_base ?? '')
  }

  // A single dialog instance is reused across whichever node's inspector (or
  // the Profile page's credentials list) opens it -- re-sync to the
  // newly-requested provider each time it opens, since a plain useState
  // initializer only applies on first mount.
  // selectProvider is deliberately not a dependency here -- it's a plain
  // function recreated every render, and depending on it would re-run this
  // effect (and reset azureProjectEndpoint) on every keystroke.
  useEffect(() => {
    if (open) selectProvider(defaultProvider)
  }, [open, defaultProvider, settingsQuery.data])

  // Saving stores the key without validating it (upsert_setting only
  // encrypts), so a typo'd key used to sit there silently until a real run
  // failed. Right after a successful save is the moment for this -- it's the
  // one point where the user is looking at the field they just filled in, so a
  // failure is attributable to what they typed. Cheap enough to be automatic:
  // one free list call, no tokens.
  const check = useConnectionCheck(provider)

  const saveMutation = useMutation({
    mutationFn: () =>
      llmSettingsApi.upsert({
        provider: provider!,
        api_key: apiKey,
        // api_base is derived server-side from azure_project_endpoint for
        // azure_foundry -- not sent here at all for that provider (see
        // upsert_setting's own comment). Only local's own field (below) is
        // wired up here; anthropic/openai/openrouter keep their well-known
        // defaults with no UI to override one, same as before this provider
        // existed.
        api_base: requiresApiBase ? apiBase || null : null,
        azure_project_endpoint: requiresProjectEndpoint ? azureProjectEndpoint || null : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-settings'] })
      // Deliberately NOT closing the dialog here any more: the check reads
      // the credential back from the server, so it can only run once the
      // save has landed, and its result needs somewhere to be seen.
      check.mutate()
    },
  })

  function reset() {
    setQuery('')
    setProvider(null)
    setApiKey('')
    setAzureProjectEndpoint('')
    setApiBase('')
    saveMutation.reset()
    check.reset()
  }

  // Editing any field after a save invalidates the result on screen -- a
  // green "Key valid" sitting above a key that's since been retyped is the
  // stale-indicator problem this feature exists to avoid.
  function clearResultOnEdit() {
    if (saveMutation.isSuccess || saveMutation.isError) {
      saveMutation.reset()
      check.reset()
    }
  }

  const filtered = PROVIDER_CATALOG.filter((p) => p.label.toLowerCase().includes(query.trim().toLowerCase()))

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent className={cn('sm:max-w-md', HUD_ACCENT_RING_CLASSNAME)}>
        <DialogHeader>
          <DialogTitle>New credential</DialogTitle>
          <DialogDescription>
            {provider ? 'Enter your provider settings.' : 'Search for an LLM provider to connect.'}
          </DialogDescription>
        </DialogHeader>

        {!provider ? (
          <div className="space-y-3">
            <Input autoFocus placeholder="Search providers…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <div className="flex flex-col gap-1.5">
              {filtered.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No matching providers.</p>}
              {filtered.map((p) => {
                const Icon = PROVIDER_META[p.id]?.icon ?? Cloud
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => selectProvider(p.id)}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg border bg-background px-3 py-2.5 text-left text-sm shadow-[0_0_16px_-6px_var(--primary)] ring-1 ring-primary/20 transition-colors hover:bg-muted"
                  >
                    <Icon className="size-4 shrink-0 text-primary" />
                    <div className="min-w-0">
                      <p className="font-medium">{p.label}</p>
                      <p className="truncate text-xs text-muted-foreground">{p.description}</p>
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border bg-muted/30 px-3 py-2">
              <span className="text-sm font-medium">{PROVIDER_CATALOG.find((p) => p.id === provider)?.label}</span>
              <Button variant="ghost" size="icon-sm" aria-label="Change provider" onClick={() => setProvider(null)}>
                <X className="size-4" />
              </Button>
            </div>

            {existing && (
              <p className="text-xs text-muted-foreground">
                You already have a saved {PROVIDER_CATALOG.find((p) => p.id === provider)?.label} credential — saving will
                replace it.
              </p>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="credential-api-key">API key{apiKeyOptional && ' (optional)'}</Label>
              <PasswordInput
                id="credential-api-key"
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value)
                  clearResultOnEdit()
                }}
              />
              <p className="text-xs text-muted-foreground">
                {apiKeyOptional
                  ? "Most self-hosted servers don't check this -- leave it blank unless yours does."
                  : 'Encrypted at rest before storage -- only decrypted at the moment a run needs it, never logged.'}
              </p>
            </div>

            {requiresApiBase && (
              <div className="space-y-1.5">
                <Label htmlFor="credential-api-base">Base URL</Label>
                <Input
                  id="credential-api-base"
                  placeholder="http://localhost:8000/v1"
                  value={apiBase}
                  onChange={(e) => {
                    setApiBase(e.target.value)
                    clearResultOnEdit()
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Your server's OpenAI-compatible base URL -- there's no default host for a self-hosted server.
                </p>
              </div>
            )}

            {requiresProjectEndpoint && (
              <div className="space-y-1.5">
                <Label htmlFor="credential-azure-project-endpoint">Project endpoint</Label>
                <Input
                  id="credential-azure-project-endpoint"
                  placeholder="https://my-resource.services.ai.azure.com/api/projects/my-project"
                  value={azureProjectEndpoint}
                  onChange={(e) => {
                    setAzureProjectEndpoint(e.target.value)
                    clearResultOnEdit()
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Copy this from the Foundry portal's connection info -- used to both run agents and list your
                  deployments.
                </p>
              </div>
            )}

            {saveMutation.isError && <p className="text-sm text-destructive">Could not save this credential. Please try again.</p>}

            {saveMutation.isSuccess && (
              <div className="space-y-1.5 rounded-lg border bg-muted/30 px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">Credential saved</span>
                  {check.isPending && (
                    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <LoaderCircle className="size-3.5 animate-spin" />
                      Testing connection…
                    </span>
                  )}
                  {check.data && <ConnectionStatusBadge status={check.data.status} />}
                </div>
                {check.data && (
                  <p className="text-xs text-muted-foreground">
                    {check.data.detail}
                    {check.data.endpoint && (
                      <span className="mt-0.5 block truncate font-mono" title={check.data.endpoint}>
                        {check.data.endpoint}
                      </span>
                    )}
                  </p>
                )}
                {check.isError && (
                  // The credential itself did save -- only the follow-up check
                  // couldn't run. Say so, or this reads as a failed save.
                  <p className="text-xs text-muted-foreground">
                    Saved, but the connection check couldn&apos;t run. Test it from Profile → LLM credentials.
                  </p>
                )}
              </div>
            )}

            <DialogFooter>
              {saveMutation.isSuccess ? (
                <Button
                  onClick={() => {
                    reset()
                    onOpenChange(false)
                  }}
                >
                  Done
                </Button>
              ) : (
                <Button
                  onClick={() => saveMutation.mutate()}
                  disabled={
                    (!apiKeyOptional && !apiKey.trim()) ||
                    (requiresProjectEndpoint && !azureProjectEndpoint.trim()) ||
                    (requiresApiBase && !apiBase.trim()) ||
                    saveMutation.isPending
                  }
                >
                  {saveMutation.isPending ? 'Saving…' : 'Save credential'}
                </Button>
              )}
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
