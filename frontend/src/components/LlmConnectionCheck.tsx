import { useMutation } from '@tanstack/react-query'
import { CircleCheck, CircleHelp, CircleX } from 'lucide-react'
import { llmSettingsApi } from '@/api/client'
import { cn } from '@/lib/utils'
import type { LLMConnectionStatus, LLMProvider } from '@/types/llmSettings'

// Shared by all three places a credential's health is shown -- the Profile
// page's credentials table, the save step in CreateCredentialDialog, and the
// LLM node inspector's Credential field. One definition on purpose: the same
// status must always resolve to the same color and the same word everywhere
// (the root CLAUDE.md's rule for status-driven tint), and "Key valid" in one
// place with "Connected" in another would read as two different claims.
//
// Colors follow the app's status language: emerald for done/good, amber for
// indeterminate, destructive for broken. "unknown" is deliberately amber and
// not red -- an Azure credential with no project endpoint to list, or a
// provider that answered 429, tells us nothing bad about the key.
export const CONNECTION_STATUS_META: Record<
  LLMConnectionStatus,
  { label: string; color: string; Icon: typeof CircleCheck }
> = {
  // Not "Ready" or "Connected": a free list call proves the key, the network
  // and the org, but quota/billing/per-project model access are only
  // enforced at inference time, so a key can pass here and still fail on the
  // first real request. Don't promise more than was tested.
  ok: { label: 'Key valid', color: 'var(--chart-3)', Icon: CircleCheck },
  failed: { label: 'Failed', color: 'var(--destructive)', Icon: CircleX },
  unknown: { label: 'Inconclusive', color: 'var(--chart-4)', Icon: CircleHelp },
}

/** GET /llm-settings/{provider}/connection -- a zero-token liveness check
 * (a free authenticated list call per provider, never an inference request).
 *
 * A mutation rather than a query, in all three call sites, because the result
 * is deliberately NOT cached: "is my key working right now" is a question you
 * ask, and a stale green dot from ten minutes ago is worse than no dot. It
 * also keeps this off the render path -- it costs no tokens, but it's still
 * one outbound request against a rate-limited endpoint (10/min per provider).
 */
export function useConnectionCheck(provider: LLMProvider | null | undefined) {
  return useMutation({ mutationFn: () => llmSettingsApi.testConnection(provider!) })
}

export function ConnectionStatusBadge({
  status,
  className,
}: {
  status: LLMConnectionStatus
  className?: string
}) {
  const meta = CONNECTION_STATUS_META[status]
  return (
    <span
      className={cn('flex items-center gap-1.5 text-xs font-medium', className)}
      style={{ color: meta.color }}
    >
      <meta.Icon className="size-3.5 shrink-0" />
      {meta.label}
    </span>
  )
}
