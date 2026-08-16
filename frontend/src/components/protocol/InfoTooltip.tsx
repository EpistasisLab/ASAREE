import type { ReactNode } from 'react'
import { Info } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

// A small "what is this for" hint next to a field Label -- a themed Tooltip
// (not the browser's native `title`, same reasoning as FactorBindableField's
// own trigger and WarningBadge: slow, inconsistent chrome). Place inline
// right after the Label text, e.g. `<Label className="flex items-center
// gap-1.5">Replicates<InfoTooltip>...</InfoTooltip></Label>`.
export function InfoTooltip({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider delay={200}>
      <Tooltip>
        <TooltipTrigger
          render={<button type="button" className="text-muted-foreground hover:text-foreground" aria-label="More info" />}
        >
          <Info className="size-3" />
        </TooltipTrigger>
        <TooltipContent className="max-w-64 flex-col items-start gap-1 text-left">{children}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
