import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

const DEFAULT_WIDTH = 320
const MIN_WIDTH = 320
const MAX_WIDTH = 1100
const MIN_CANVAS_WIDTH = 420
const STORAGE_KEY = 'asaree:node-selection-drawer-width'

function storedWidth() {
  const raw = window.localStorage.getItem(STORAGE_KEY)
  const parsed = raw === null ? NaN : Number(raw)
  return Number.isFinite(parsed) ? Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, parsed)) : DEFAULT_WIDTH
}

// Own the width above the catalog/browser drill-down so navigating between
// node types keeps the same layout. Measure the actual canvas container:
// the independently resizable experiment panel already occupies viewport space.
export function NodeSelectionDrawer({ children }: { children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null)
  const [preferredWidth, setPreferredWidth] = useState(storedWidth)
  const [availableWidth, setAvailableWidth] = useState(window.innerWidth)
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ x: number; width: number; latest: number } | null>(null)
  const maxWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, availableWidth - MIN_CANVAS_WIDTH))
  const width = Math.round(Math.min(maxWidth, Math.max(MIN_WIDTH, preferredWidth)))

  useEffect(() => {
    const parent = container.current?.parentElement
    if (!parent) return
    const measure = () => setAvailableWidth(parent.getBoundingClientRect().width)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(parent)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!dragging) return
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
  }, [dragging])

  function changeWidth(next: number, persist = false) {
    const clamped = Math.round(Math.min(maxWidth, Math.max(MIN_WIDTH, next)))
    setPreferredWidth(clamped)
    if (persist) window.localStorage.setItem(STORAGE_KEY, String(clamped))
    return clamped
  }

  function beginDrag(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { x: event.clientX, width, latest: width }
    setDragging(true)
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return
    // The drawer is on the right: moving its left edge left makes it wider.
    drag.current.latest = changeWidth(drag.current.width + drag.current.x - event.clientX)
  }

  function endDrag() {
    if (!drag.current) return
    window.localStorage.setItem(STORAGE_KEY, String(drag.current.latest))
    drag.current = null
    setDragging(false)
  }

  return <div ref={container} className="relative flex min-h-0 min-w-0 shrink-0" style={{ width }}>
    {children}
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the node selection drawer"
      aria-valuenow={width}
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={maxWidth}
      tabIndex={0}
      onPointerDown={beginDrag}
      onPointerMove={moveDrag}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onDoubleClick={() => changeWidth(DEFAULT_WIDTH, true)}
      onKeyDown={event => {
        if (event.key === 'ArrowLeft') changeWidth(width + 24, true)
        else if (event.key === 'ArrowRight') changeWidth(width - 24, true)
        else return
        event.preventDefault()
      }}
      title="Drag to resize — double-click to reset"
      className="group/resize absolute top-0 -left-1.5 z-20 flex h-full w-3 cursor-col-resize touch-none items-center justify-center focus:outline-none"
    >
      <div className={cn(
        'h-full w-px rounded-full transition-[width,background-color,box-shadow] duration-150',
        dragging ? 'w-0.5 bg-primary shadow-[0_0_8px_var(--primary)]'
          : 'bg-border group-hover/resize:w-0.5 group-hover/resize:bg-primary group-hover/resize:shadow-[0_0_8px_var(--primary)] group-focus-visible/resize:w-0.5 group-focus-visible/resize:bg-primary group-focus-visible/resize:shadow-[0_0_8px_var(--primary)]',
      )} />
    </div>
  </div>
}
