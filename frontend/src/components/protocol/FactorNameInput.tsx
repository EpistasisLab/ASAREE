import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { displayFactorName } from '@/lib/factorLabels'

/** Reject typed and pasted colons; opening a legacy name never renames it. */
export function FactorNameInput({ value, onValueChange, ...props }: {
  id: string
  value: string
  disabled?: boolean
  onValueChange: (value: string) => void
}) {
  const [rejected, setRejected] = useState(false)
  const errorId = `${props.id}-error`
  return <>
    <Input {...props} value={displayFactorName(value)} aria-describedby={rejected ? errorId : undefined}
      onChange={event => {
        const next = event.target.value
        if (next.includes(':')) {
          setRejected(true)
          return
        }
        setRejected(false)
        onValueChange(next)
      }} />
    {rejected && <p id={errorId} role="alert" className="text-xs text-destructive">Colons (:) aren’t allowed in factor names. Use › to separate a node and field.</p>}
  </>
}
