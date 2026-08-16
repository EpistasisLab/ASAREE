import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Cloud, X } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
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

  // Selecting a provider (either directly from the search list, or via
  // defaultProvider when opened from an existing credential's own "Edit")
  // pre-fills its non-secret fields from any already-saved setting -- the
  // API key stays blank either way, since it's write-only and never
  // echoed back by the API.
  function selectProvider(id: LLMProvider | null) {
    setProvider(id)
    const existingSetting = id ? settingsQuery.data?.find((s) => s.provider === id) : undefined
    setAzureProjectEndpoint(existingSetting?.azure_project_endpoint ?? '')
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

  const saveMutation = useMutation({
    mutationFn: () =>
      llmSettingsApi.upsert({
        provider: provider!,
        api_key: apiKey,
        // api_base is derived server-side from azure_project_endpoint --
        // not sent here at all (see upsert_setting's own comment).
        azure_project_endpoint: requiresProjectEndpoint ? azureProjectEndpoint || null : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-settings'] })
      reset()
      onOpenChange(false)
    },
  })

  function reset() {
    setQuery('')
    setProvider(null)
    setApiKey('')
    setAzureProjectEndpoint('')
    saveMutation.reset()
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
              <Label htmlFor="credential-api-key">API key</Label>
              <PasswordInput id="credential-api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
              <p className="text-xs text-muted-foreground">
                Encrypted at rest before storage -- only decrypted at the moment a run needs it, never logged.
              </p>
            </div>

            {requiresProjectEndpoint && (
              <div className="space-y-1.5">
                <Label htmlFor="credential-azure-project-endpoint">Project endpoint</Label>
                <Input
                  id="credential-azure-project-endpoint"
                  placeholder="https://my-resource.services.ai.azure.com/api/projects/my-project"
                  value={azureProjectEndpoint}
                  onChange={(e) => setAzureProjectEndpoint(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Copy this from the Foundry portal's connection info -- used to both run agents and list your
                  deployments.
                </p>
              </div>
            )}

            {saveMutation.isError && <p className="text-sm text-destructive">Could not save this credential. Please try again.</p>}

            <DialogFooter>
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={!apiKey.trim() || (requiresProjectEndpoint && !azureProjectEndpoint.trim()) || saveMutation.isPending}
              >
                {saveMutation.isPending ? 'Saving…' : 'Save credential'}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
