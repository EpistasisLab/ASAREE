import { ArrowLeft, ArrowRight } from 'lucide-react'
import { PREVIOUS_TOKEN, referencedSenderIds, usesPreviousToken, type HandoffPeers } from '@/lib/promptReferences'

// Who hands off to this agent, and who it hands off to -- split into two
// components because the Agent inspector puts them at opposite ends of itself:
// Receives heads the Input pane, Sends heads the Output pane, so each sits with
// the data it describes rather than both floating above the prompt.
//
// This is the readout that makes the model legible: it says that step 5 does
// NOT see step 1, which is the single most surprising thing about a chain, and
// -- since an edge only grants availability -- it says which of the senders
// this prompt actually pulls in. A wired-but-unreferenced sender looks
// identical on the canvas to a referenced one, so without this the only way to
// find out is to read a finished run's transcript.
//
// Direct neighbours only, mirroring `_upstream_ids`: connector nodes (LLM,
// Dataset, Memory, Tool) are never on a main edge and so never appear here,
// and a reach-back to an earlier step is a separate, deliberate act that the
// reference picker covers.

const ROW_CLASSNAME = 'space-y-1.5 rounded-md border bg-muted/20 p-3 font-mono text-xs'

export function ReceivesSummary({ peers, prompt }: { peers: HandoffPeers; prompt: string }) {
  const referenced = referencedSenderIds(prompt, peers.receives)
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
                  {!referenced.has(target.id) && (
                    // Not an error -- a step whose prompt stands alone is a
                    // legitimate design, and it may be exactly the treatment
                    // being tested. It just must not be a surprise.
                    <span
                      className="rounded-sm bg-[color:var(--chart-4)]/10 px-1 py-px text-[0.65rem] text-[color:var(--chart-4)]"
                      title={`Wired to this agent, but this prompt never references ${target.name}, so its output is not passed along. Insert a reference to pass it.`}
                    >
                      not referenced
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
      {/* Only the senders this prompt actually pulls in: a shape promised by a
          node whose output never arrives is not something to reconcile against.
          Shown to the user, not to the agent -- see `HandoffPeer.expectedOutput`
          for why the consuming model is deliberately not told. */}
      {peers.receives
        .filter((target) => target.expectedOutput && referenced.has(target.id))
        .map((target) => (
          <p key={target.id} className="pt-0.5 text-muted-foreground">
            <span className="text-[color:var(--node-label)]">{target.name}</span> promises:{' '}
            <span className="italic">{target.expectedOutput}</span>
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
