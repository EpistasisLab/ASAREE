import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'

interface Position {
  x: number
  y: number
}

// Pointer-drag behavior shared by the movable canvas overlays. Positions are
// offsets from each panel's authored anchor (top-left for the conversation,
// bottom-right for results), so both keep their sensible initial placement.
export function useDraggableOverlay<T extends HTMLElement = HTMLElement>({
  recomputeKey,
}: {
  recomputeKey?: string | number | boolean
} = {}) {
  const panelRef = useRef<T>(null)
  const [position, setPosition] = useState<Position>({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const preservedPosition = useRef<Pick<DOMRect, 'left' | 'top'> | null>(null)
  const drag = useRef<{
    pointerId: number
    clientX: number
    clientY: number
    position: Position
    panelRect: DOMRect
    boundaryRect: DOMRect | null
  } | null>(null)

  useEffect(() => {
    if (!dragging) return
    const previousCursor = document.body.style.cursor
    const previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'move'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousSelect
    }
  }, [dragging])

  const placeInsideBoundary = useCallback((preserve?: Pick<DOMRect, 'left' | 'top'> | null) => {
    const panel = panelRef.current
    const boundary = panel?.offsetParent
    if (!panel || !(boundary instanceof HTMLElement)) return
    const panelRect = panel.getBoundingClientRect()
    const boundaryRect = boundary.getBoundingClientRect()
    if (boundaryRect.width <= 0 || boundaryRect.height <= 0) return

    let shiftX = preserve ? preserve.left - panelRect.left : 0
    let shiftY = preserve ? preserve.top - panelRect.top : 0
    const shiftedLeft = panelRect.left + shiftX
    const shiftedRight = panelRect.right + shiftX
    const shiftedTop = panelRect.top + shiftY
    const shiftedBottom = panelRect.bottom + shiftY
    if (shiftedLeft < boundaryRect.left) shiftX += boundaryRect.left - shiftedLeft
    else if (shiftedRight > boundaryRect.right) shiftX += boundaryRect.right - shiftedRight
    if (shiftedTop < boundaryRect.top) shiftY += boundaryRect.top - shiftedTop
    else if (shiftedBottom > boundaryRect.bottom) shiftY += boundaryRect.bottom - shiftedBottom
    if (shiftX || shiftY) {
      setPosition((current) => ({ x: current.x + shiftX, y: current.y + shiftY }))
    }
  }, [])

  const clampInsideBoundary = useCallback(() => placeInsideBoundary(), [placeInsideBoundary])

  const preservePositionOnNextLayout = () => {
    const rect = panelRef.current?.getBoundingClientRect()
    preservedPosition.current = rect ? { left: rect.left, top: rect.top } : null
  }

  // A bottom-anchored minimized panel grows upward when restored. Re-clamp
  // after that layout change so its drag handle can never expand above the
  // canvas and become unreachable. Viewport resizes need the same correction.
  useLayoutEffect(() => {
    const preserve = preservedPosition.current
    preservedPosition.current = null
    placeInsideBoundary(preserve)
  }, [placeInsideBoundary, recomputeKey])
  useEffect(() => {
    window.addEventListener('resize', clampInsideBoundary)
    return () => window.removeEventListener('resize', clampInsideBoundary)
  }, [clampInsideBoundary])

  const finishDrag = () => {
    drag.current = null
    setDragging(false)
  }

  const handleProps = {
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return
      event.currentTarget.setPointerCapture?.(event.pointerId)
      const panel = panelRef.current
      if (!panel) return
      drag.current = {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        position,
        panelRect: panel.getBoundingClientRect(),
        boundaryRect: panel.offsetParent instanceof HTMLElement ? panel.offsetParent.getBoundingClientRect() : null,
      }
      setDragging(true)
    },
    onPointerMove: (event: ReactPointerEvent<HTMLElement>) => {
      const start = drag.current
      if (!start || start.pointerId !== event.pointerId) return
      let deltaX = event.clientX - start.clientX
      let deltaY = event.clientY - start.clientY
      const boundary = start.boundaryRect
      if (boundary && boundary.width > 0 && boundary.height > 0) {
        const minLeft = boundary.left
        const maxLeft = Math.max(minLeft, boundary.right - start.panelRect.width)
        const minTop = boundary.top
        const maxTop = Math.max(minTop, boundary.bottom - start.panelRect.height)
        deltaX = Math.min(maxLeft, Math.max(minLeft, start.panelRect.left + deltaX)) - start.panelRect.left
        deltaY = Math.min(maxTop, Math.max(minTop, start.panelRect.top + deltaY)) - start.panelRect.top
      }
      setPosition({ x: start.position.x + deltaX, y: start.position.y + deltaY })
    },
    onPointerUp: finishDrag,
    onPointerCancel: finishDrag,
  }

  const style: CSSProperties = { transform: `translate3d(${position.x}px, ${position.y}px, 0)` }
  return { panelRef, handleProps, preservePositionOnNextLayout, style }
}
