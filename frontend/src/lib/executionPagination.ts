import { useCallback, useState } from 'react'

export function useCursor() {
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined])
  const next = useCallback((cursor: string) => setCursors(current => [...current, cursor]), [])
  const previous = useCallback(() => setCursors(current => current.slice(0, -1)), [])
  const reset = useCallback(() => setCursors([undefined]), [])
  return { cursor: cursors.at(-1), back: cursors.length > 1, next, previous, reset }
}
