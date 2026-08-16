import type { CSSProperties } from "react"
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** A `style` prop re-tinting a `Card`'s glow/ring/corner-brackets — pass a
 * color, e.g. `cardAccent('var(--chart-3)')`. React's CSSProperties has no
 * slot for arbitrary custom properties, hence the cast. */
export function cardAccent(color: string): CSSProperties {
  return { '--card-accent': color } as CSSProperties
}

/** The glow + tinted ring + corner-bracket recipe every HUD surface in this
 * app shares (`Card`, node inspectors, credential/factor dialogs) -- keyed
 * off `--card-accent` (see `cardAccent()`) falling back to `--primary`.
 * Deliberately excludes any `position` utility: every consumer already
 * establishes its own positioning context (`Card`'s own `relative`, a
 * `Dialog`'s own `fixed`), and bundling one here risks `tailwind-merge`
 * treating it as conflicting with a caller's own positioning class
 * depending on argument order. */
export const HUD_ACCENT_RING_CLASSNAME =
  "ring-1 ring-[color:var(--card-accent,var(--primary))]/15 shadow-[0_0_24px_-12px_var(--card-accent,var(--primary))] before:pointer-events-none before:absolute before:top-1 before:left-1 before:size-3 before:border-t-2 before:border-l-2 before:border-[color:var(--card-accent,var(--primary))]/60 before:content-[''] after:pointer-events-none after:absolute after:right-1 after:bottom-1 after:size-3 after:border-r-2 after:border-b-2 after:border-[color:var(--card-accent,var(--primary))]/60 after:content-['']"

const CHART_HUES = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

/** Deterministically maps a string (e.g. a model name) to one of the theme's
 * five chart hues — same input always gets the same color, with no need for
 * a hardcoded name→color table that goes stale the moment a new model ships. */
export function hashToChartHue(input: string): string {
  let hash = 0
  for (let i = 0; i < input.length; i++) hash = (hash * 31 + input.charCodeAt(i)) | 0
  return CHART_HUES[Math.abs(hash) % CHART_HUES.length]
}
