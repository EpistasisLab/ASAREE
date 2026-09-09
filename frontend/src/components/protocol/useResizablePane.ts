import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

// A drag-resizable side pane of a node inspector, remembered across sessions.
//
// Extracted once the Agent inspector grew a second one: Input on the left and
// Output on the right are the same affordance mirrored, and hand-rolling the
// second copy would have meant two subtly different clamps. `side` is the only
// thing that differs -- a handle on a pane's right edge grows it as the pointer
// moves right, one on its left edge grows it as the pointer moves left.
//
// Not shared with `ExperimentSidePanel`, which has its own collapse state,
// keyboard stepping and double-click reset; this is the plain version.
export function useResizablePane({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
  side,
  resolveMaxWidth,
  recomputeKey,
}: {
  storageKey: string
  defaultWidth: number
  minWidth: number
  maxWidth: number
  /** Which side of the container the pane sits on -- i.e. which of its own
   *  edges the handle is attached to. */
  side: 'left' | 'right'
  /** A tighter ceiling resolved from the live container, so two panes can't
   *  squeeze the middle column out of existence. Read at drag start rather
   *  than on every pointer move -- the container can't resize mid-drag -- and
   *  again whenever `recomputeKey` or the viewport changes. */
  resolveMaxWidth?: () => number
  /** Changes when the container appears or is swapped for a different one, so
   *  a width stored on a wide monitor gets re-clamped against the frame it is
   *  actually being shown in. Mount alone isn't enough: the container often
   *  isn't rendered yet on the render that first calls this hook. */
  recomputeKey?: string | number
}) {
  const [width, setWidth] = useState(() => {
    const raw = typeof window !== 'undefined' ? Number(window.localStorage.getItem(storageKey)) : NaN
    // `> 0` and not just `isFinite`: a missing key reads as `null`, which
    // `Number` turns into 0 -- an honest-looking value that would silently pin
    // a first-time pane to its minimum instead of its default.
    return Number.isFinite(raw) && raw > 0 ? Math.min(maxWidth, Math.max(minWidth, raw)) : defaultWidth
  })
  const [resizing, setResizing] = useState(false)
  const drag = useRef<{ x: number; width: number; max: number } | null>(null)
  // The pointer-up handler persists whatever the last move produced; reading it
  // from a ref keeps that independent of when React re-renders.
  const latest = useRef(width)
  latest.current = width
  const ceiling = useRef(resolveMaxWidth)
  ceiling.current = resolveMaxWidth

  // Whatever the ceiling currently is, never below the floor -- a container too
  // narrow for both panes clamps them to their minimums rather than inverting.
  const clampToRoom = useCallback(
    () => Math.max(minWidth, Math.min(maxWidth, ceiling.current?.() ?? maxWidth)),
    [minWidth, maxWidth],
  )

  // A width stored on a wide monitor has to be re-fitted to the frame it is
  // being shown in, or the two panes together push the middle column to
  // nothing. Only ever shrinks: re-widening would undo a deliberate drag.
  useEffect(() => {
    const fit = () => setWidth((current) => Math.min(current, clampToRoom()))
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
    // `recomputeKey` is a destructured parameter of this hook, which the linter
    // misreads as an outer-scope value; it is exactly the dependency that makes
    // a re-fit happen once the container exists.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recomputeKey, clampToRoom])

  useEffect(() => {
    if (!resizing) return
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
  }, [resizing])

  const handleProps = {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => {
      event.currentTarget.setPointerCapture(event.pointerId)
      drag.current = { x: event.clientX, width: latest.current, max: clampToRoom() }
      setResizing(true)
    },
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => {
      const start = drag.current
      if (!start) return
      const delta = side === 'left' ? event.clientX - start.x : start.x - event.clientX
      setWidth(Math.round(Math.min(start.max, Math.max(minWidth, start.width + delta))))
    },
    onPointerUp: () => {
      if (!drag.current) return
      drag.current = null
      setResizing(false)
      window.localStorage.setItem(storageKey, String(latest.current))
    },
    onPointerCancel: () => {
      if (!drag.current) return
      drag.current = null
      setResizing(false)
      window.localStorage.setItem(storageKey, String(latest.current))
    },
  }

  return { width, resizing, handleProps }
}

/** The shared look of an inspector's vertical drag handle: a hairline in a
 *  16px-wide hit area, which is also the gutter between two panes. */
export const RESIZE_HANDLE_CLASSNAME =
  'relative w-4 shrink-0 cursor-col-resize touch-none rounded hover:bg-primary/10 before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-border hover:before:bg-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
