import { ArrowLeft, ArrowRight } from 'lucide-react'
import { PREVIOUS_TOKEN, referencedSenderIds, usesPreviousToken, type HandoffPeers } from '@/lib/promptReferences'

// Who hands off to this agent, and who it hands off to -- split into two
// components because the Agent inspector puts them at opposite ends of itself:
// Receives heads the Input pane, Sends heads the Output pane, so each sits with
// the data it describes rather than both floating above the prompt.
//
// This is the readout that makes the model legible: it says that step 5 does
// NOT see step 1, which is the single most surprising thing about a chain.
// Everything it lists under Receives arrives -- drawing the edge is the
// request -- so this is the answer to "what is actually in my prompt", which
// otherwise takes reading a finished run's transcript.
//
// Direct neighbours only, mirroring `_upstream_ids`: connector nodes (LLM,
// Dataset, Memory, Tool) are never on a main edge and so never appear here,
// and a reach-back to an earlier step is a separate, deliberate act that the
// reference picker covers.

const ROW_CLASSNAME = 'space-y-1.5 rounded-md border bg-muted/20 p-3 font-mono text-xs'

export function ReceivesSummary({ peers, prompt }: { peers: HandoffPeers; prompt: string }) {
  // Where each sender lands, not whether it lands: one named in the prompt is
  // rendered at that spot, one that is not is appended after it.
  const placed = referencedSenderIds(prompt, peers.receives)
  const usesPrevious = peers.receives.length > 0 && usesPreviousToken(prompt)

  return (
    <div className={ROW_CLASSNAME}>
      <div className="flex gap-2">
        <ArrowLeft className="mt-0.5 size-3 shrink-0 text-[color:var(--node-label)]" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          {peers.receives.length === 0 ? (
            <span className="text-muted-foreground">Nothing — this is the first step.</span>
          ) : (
            <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              {peers.receives.map((target) => (
                <span key={target.id} className="flex items-baseline gap-1.5">
                  <span className="break-all">{target.name}</span>
                  {placed.has(target.id) && (
                    // Purely positional, and dim on purpose: it answers "why is
                    // this one not at the bottom with the others", which is
                    // only a question once a prompt mixes the two.
                    <span
                      className="rounded-sm bg-muted px-1 py-px text-[0.65rem] text-muted-foreground"
                      title={`This prompt references ${target.name}, so its output is placed where you wrote it instead of being appended at the end.`}
                    >
                      inline
                    </span>
                  )}
                </span>
              ))}
            </span>
          )}
        </span>
      </div>
      {usesPrevious && (
        // The one reference whose meaning depends on the wiring rather than on
        // what is written, so the wiring is where it has to be expanded.
        <p className="pt-0.5 text-muted-foreground">
          {PREVIOUS_TOKEN} = {peers.receives.map((t) => t.name).join(' + ')}
        </p>
      )}
      {/* Shown to the user, not to the agent -- see `HandoffPeer` for why the
          consuming model is deliberately not told. Field names rather than the
          prose promise this used to print: the shape a sender declares is now
          its Output Parser's field list, which is also exactly what can be
          referenced individually, so this doubles as the menu for doing so. */}
      {peers.receives
        .filter((target) => target.fields?.length)
        .map((target) => (
          <p key={target.id} className="pt-0.5 text-muted-foreground">
            <span className="text-[color:var(--node-label)]">{target.name}</span> promises:{' '}
            <span className="text-foreground">{target.fields!.join(', ')}</span>
          </p>
        ))}
    </div>
  )
}

export function SendsSummary({ peers }: { peers: HandoffPeers }) {
  return (
    <div className={ROW_CLASSNAME}>
      <div className="flex gap-2">
        <ArrowRight className="mt-0.5 size-3 shrink-0 text-[color:var(--node-label)]" aria-hidden="true" />
        <span className="min-w-0 flex-1">
          {peers.sends.length === 0 ? (
            <span className="text-muted-foreground">Nothing downstream — this is the final output.</span>
          ) : (
            // Availability only: whether each of these actually uses what it is
            // handed is a fact about *its* prompt, which is stated in its own
            // inspector rather than guessed at from here.
            <span className="break-all">{peers.sends.map((t) => t.name).join(', ')}</span>
          )}
        </span>
      </div>
    </div>
  )
}
