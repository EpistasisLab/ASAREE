import { useCallback, useState, type SetStateAction } from 'react'

// Prop refreshes are not edits. Keep one atomic draft so levels and labels
// refresh together, and preserve user changes until the parent acknowledges
// that draft or a new editing session opens.
export function useDialogDraft<T>(initial: T, { open = true, sessionKey = '', signatureOf = JSON.stringify }: {
  open?: boolean
  sessionKey?: string
  signatureOf?: (draft: T) => string
} = {}) {
  const signature = signatureOf(initial)
  const [state, setState] = useState(() => ({ draft: initial, signature, open, sessionKey, dirty: false }))
  let current = state
  const newSession = (open && !state.open) || sessionKey !== state.sessionKey
  const acknowledged = state.dirty && signatureOf(state.draft) === signature
  // Reconcile before autosave sees the draft; an effect would expose stale
  // state for one render after opening or receiving new props.
  if (newSession || acknowledged || (!state.dirty && signature !== state.signature)) {
    current = { draft: initial, signature, open, sessionKey, dirty: false }
    setState(current)
  } else if (open !== state.open || signature !== state.signature) {
    current = { ...state, open, signature }
    setState(current)
  }

  const setDraft = useCallback((value: SetStateAction<T>) => {
    setState((previous) => ({
      ...previous,
      draft: typeof value === 'function' ? (value as (previous: T) => T)(previous.draft) : value,
      dirty: true,
    }))
  }, [])
  const reset = useCallback((draft: T) => {
    setState((previous) => ({ ...previous, draft, dirty: false }))
  }, [])
  return { draft: current.draft, setDraft, dirty: current.dirty, reset }
}
