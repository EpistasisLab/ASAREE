import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { Conversation, ConversationMessage } from '@/types/protocols'

// The literal id the backend uses for the human on both ends of a
// conversation: the opening question comes from "user", and the entry agent's
// final answer goes back to it (services/agent_messenger.py's
// USER_PARTICIPANT).
const USER_PARTICIPANT = 'user'

// A2A TaskState, as the messenger records it on each reply. Only a reply
// carries one -- a request has no outcome yet -- and only the ones that mean
// something went sideways are worth colouring.
const REPLY_BADGE: Record<string, { label: string; className: string }> = {
  completed: { label: 'answered', className: 'text-[color:var(--chart-3)]' },
  failed: { label: 'failed', className: 'text-destructive' },
  canceled: { label: 'cancelled', className: 'text-muted-foreground' },
  rejected: { label: 'refused', className: 'text-[color:var(--chart-4)]' },
}

const CONVERSATION_STATE_LABEL: Record<string, string> = {
  working: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Cancelled',
  limit_reached: 'Limit reached',
}

// Parts, not a bare string: the backend already speaks A2A's part shape, so a
// future data or file part is a new branch here rather than a rewrite of every
// caller. Non-text parts are named rather than dropped -- a transcript that
// silently omits half a reply is worse than one that says "1 data part".
function MessageBody({ parts }: { parts: ConversationMessage['parts'] }) {
  return (
    <>
      {parts.map((part, i) => {
        if (part.kind === 'text') {
          return (
            <p key={i} className="whitespace-pre-wrap">
              {part.text}
            </p>
          )
        }
        return (
          <p key={i} className="font-mono text-xs text-muted-foreground">
            [{part.kind} part]
          </p>
        )
      })}
    </>
  )
}

// The live transcript of a conversation-mode run, over the canvas. Deliberately
// a flat, ordered list rather than a threaded tree: `sequence` is assigned by
// the runtime and one agent runs at a time, so the order things happened in IS
// the structure -- nesting would only re-derive what the reading order already
// shows.
export function ConversationTranscript({
  conversation,
  agentNames,
}: {
  conversation: Conversation
  // Node id -> the label shown on that node's card, so the transcript names
  // agents the way the canvas does. Ids that aren't on the canvas (a node
  // deleted since the run) fall back to the raw id rather than disappearing.
  agentNames: Map<string, string>
}) {
  const nameOf = (id: string) => (id === USER_PARTICIPANT ? 'You' : agentNames.get(id) ?? id)
  const stateLabel = CONVERSATION_STATE_LABEL[conversation.state] ?? conversation.state
  // Collapsible because the canvas now opens on the protocol's most recent run
  // rather than only on one launched in this tab, so this panel is present the
  // whole time you're editing a Peer Collaboration graph -- not just while
  // watching a run finish. Collapsed keeps the header, which is the part that
  // says a conversation happened at all.
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside className="absolute right-3 bottom-3 z-10 max-h-[45%] w-[min(28rem,calc(100%-1.5rem))] overflow-y-auto rounded-lg border border-[color:var(--node-label)]/35 bg-card/95 p-3 shadow-[0_0_16px_-6px_var(--node-label)] backdrop-blur">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex w-full items-center justify-between gap-2 rounded text-left hover:opacity-80"
        aria-expanded={!collapsed}
      >
        <p className="text-sm font-medium text-[color:var(--node-label)]">Agent conversation</p>
        <span className="flex items-center gap-1.5">
          <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{stateLabel}</span>
          <span className="text-xs text-muted-foreground">{conversation.messages.length}</span>
          {collapsed ? (
            <ChevronUp className="size-3.5 text-muted-foreground" />
          ) : (
            <ChevronDown className="size-3.5 text-muted-foreground" />
          )}
        </span>
      </button>
      <div className={collapsed ? 'hidden' : 'mt-2 space-y-2'}>
        {conversation.messages.map((message) => {
          const badge = message.state ? REPLY_BADGE[message.state] : undefined
          const failed = message.state === 'failed'
          return (
            <article
              key={message.message_id}
              className={`rounded-md border px-2.5 py-2 text-sm ${failed ? 'border-destructive/35 bg-destructive/10' : 'bg-background/60'}`}
            >
              <p className="mb-1 flex items-baseline gap-1.5 text-xs text-muted-foreground">
                <span className="font-medium">
                  {nameOf(message.from_agent_id)} → {nameOf(message.to_agent_id)}
                </span>
                {badge && <span className={badge.className}>{badge.label}</span>}
                <span className="ml-auto font-mono">#{message.sequence}</span>
              </p>
              <MessageBody parts={message.parts} />
            </article>
          )
        })}
      </div>
    </aside>
  )
}
