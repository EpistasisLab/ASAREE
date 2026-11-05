import { useEffect, useId, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

export function CopyButton({ text, label }: { text: string; label: string }) {
  const triggerId = useId()
  const tooltipId = useId()
  const [copied, setCopied] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [error, setError] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  async function copy() {
    setError(false)
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        // The Clipboard API is unavailable on plain HTTP installations.
        const previousFocus = document.activeElement
        const field = document.createElement('textarea')
        field.value = text
        field.setAttribute('aria-label', label)
        field.style.position = 'fixed'
        field.style.opacity = '0'
        document.body.append(field)
        try {
          field.select()
          if (!document.execCommand('copy')) throw new Error('Copy failed')
        } finally {
          field.remove()
          if (previousFocus instanceof HTMLElement) previousFocus.focus()
        }
      }
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
      setError(true)
    }
  }

  return <div className="shrink-0">
    <Tooltip open={copied || hovered} onOpenChange={setHovered} triggerId={triggerId}>
      <TooltipTrigger id={triggerId} render={<Button type="button" size="icon" variant="outline" onClick={copy} aria-label={label} aria-describedby={copied || hovered ? tooltipId : undefined} />}>
        {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
      </TooltipTrigger>
      <TooltipContent id={tooltipId} role="tooltip">{copied ? 'Copied' : label}</TooltipContent>
    </Tooltip>
    <span role="status" className="sr-only">{copied ? 'Copied' : ''}</span>
    {error && <p role="alert" className="mt-1 max-w-40 text-xs text-destructive">Could not copy. Select and copy the text manually.</p>}
  </div>
}
