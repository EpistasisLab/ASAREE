import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

const STORAGE_KEY = 'asaree:cell-inspector-split'
const DEFAULT_SPLIT = 40
const MIN_CELL_WIDTH = 320
const MIN_REPLICATE_WIDTH = 420
const HANDLE_WIDTH = 12

function storedSplit() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    const value = stored === null ? DEFAULT_SPLIT : Number(stored)
    return Number.isFinite(value) && value > 0 && value < 100 ? value : DEFAULT_SPLIT
  } catch {
    return DEFAULT_SPLIT
  }
}

export function ResultsInspectorSplit({ children, replicate }: { children: ReactNode; replicate?: ReactNode }) {
  const [split, setSplit] = useState(storedSplit)
  const [dragging, setDragging] = useState(false)
  const grid = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; width: number } | null>(null)

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, String(split)) } catch { /* Resizing still works without storage. */ }
  }, [split])

  useEffect(() => {
    if (!dragging) return
    const { cursor, userSelect } = document.body.style
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = cursor
      document.body.style.userSelect = userSelect
    }
  }, [dragging])

  function resize(width: number) {
    const total = grid.current?.getBoundingClientRect().width ?? 0
    if (total < 800) return
    const clamped = Math.max(MIN_CELL_WIDTH, Math.min(total - MIN_REPLICATE_WIDTH - HANDLE_WIDTH, width))
    setSplit(clamped / total * 100)
  }

  function endDrag() {
    drag.current = null
    setDragging(false)
  }

  return <div className="@container/inspectors absolute inset-0 z-20 bg-card">
    <div ref={grid} style={{ '--cell-inspector-width': `${split}%` } as CSSProperties}
      className={`grid h-full min-h-0 ${replicate ? '@min-[800px]/inspectors:grid-cols-[clamp(320px,var(--cell-inspector-width),calc(100%_-_432px))_12px_minmax(420px,1fr)]' : 'grid-cols-1'}`}>
      {children}
      {replicate && <>
        <div role="separator" aria-label="Resize cell and replicate inspectors" aria-orientation="vertical"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split)} aria-valuetext={`${Math.round(split)}% for cell details`}
          tabIndex={0} title="Drag to resize — double-click to reset"
          className="group/resize hidden h-full cursor-col-resize touch-none items-center justify-center outline-none @min-[800px]/inspectors:flex"
          onPointerDown={event => {
            if (event.button !== 0) return
            event.preventDefault()
            event.currentTarget.setPointerCapture(event.pointerId)
            drag.current = { x: event.clientX, width: grid.current?.firstElementChild?.getBoundingClientRect().width ?? 0 }
            setDragging(true)
          }}
          onPointerMove={event => {
            if (drag.current) resize(drag.current.width + event.clientX - drag.current.x)
          }}
          onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag}
          onDoubleClick={() => setSplit(DEFAULT_SPLIT)}
          onKeyDown={event => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
            event.preventDefault()
            resize((grid.current?.firstElementChild?.getBoundingClientRect().width ?? 0) + (event.key === 'ArrowLeft' ? -24 : 24))
          }}>
          <div className={`h-full w-px transition-colors ${dragging ? 'bg-primary shadow-[0_0_8px_var(--primary)]' : 'bg-border group-hover/resize:bg-primary group-focus-visible/resize:bg-primary group-focus-visible/resize:shadow-[0_0_8px_var(--primary)]'}`} />
        </div>
        {replicate}
      </>}
    </div>
  </div>
}
