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

const CHART_HUES = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)']

/** Deterministically maps a string (e.g. a model name) to one of the theme's
 * five chart hues — same input always gets the same color, with no need for
 * a hardcoded name→color table that goes stale the moment a new model ships. */
export function hashToChartHue(input: string): string {
  let hash = 0
  for (let i = 0; i < input.length; i++) hash = (hash * 31 + input.charCodeAt(i)) | 0
  return CHART_HUES[Math.abs(hash) % CHART_HUES.length]
}
