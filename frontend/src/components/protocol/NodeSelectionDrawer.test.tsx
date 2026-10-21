import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { NodeSelectionDrawer } from './NodeSelectionDrawer'

const storageKey = 'asaree:node-selection-drawer-width'
let availableWidth = 1440
let measure = () => {}

beforeEach(() => {
  availableWidth = 1440
  window.localStorage.removeItem(storageKey)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => new DOMRect(0, 0, availableWidth, 800))
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { measure = callback }
    observe() {}
    disconnect() {}
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  window.localStorage.removeItem(storageKey)
})

function setup() {
  return render(<div><NodeSelectionDrawer><div>Node browser</div></NodeSelectionDrawer></div>)
}

it('resizes with the keyboard, remembers the width, and resets on double-click', () => {
  const first = setup()
  const handle = screen.getByRole('separator', { name: 'Resize the node selection drawer' })
  fireEvent.keyDown(handle, { key: 'ArrowLeft' })
  expect(handle).toHaveAttribute('aria-valuenow', '344')
  expect(window.localStorage.getItem(storageKey)).toBe('344')
  first.unmount()
  setup()
  const restored = screen.getByRole('separator')
  expect(restored).toHaveAttribute('aria-valuenow', '344')
  fireEvent.doubleClick(restored)
  expect(restored).toHaveAttribute('aria-valuenow', '320')
  expect(window.localStorage.getItem(storageKey)).toBe('320')
  fireEvent.keyDown(restored, { key: 'ArrowRight' })
  expect(restored).toHaveAttribute('aria-valuenow', '320')
})

it('limits width against the actual canvas container as the Design panel changes size', () => {
  window.localStorage.setItem(storageKey, '900')
  availableWidth = 1000
  setup()
  const handle = screen.getByRole('separator')
  expect(handle).toHaveAttribute('aria-valuenow', '580')
  expect(handle).toHaveAttribute('aria-valuemax', '580')
  availableWidth = 1600
  act(() => measure())
  expect(handle).toHaveAttribute('aria-valuenow', '900')
  availableWidth = 800
  act(() => measure())
  fireEvent.keyDown(handle, { key: 'ArrowLeft' })
  expect(handle).toHaveAttribute('aria-valuenow', '380')
})

it('drags the left edge outward, persists its final position, and restores document styles', () => {
  const result = setup()
  const handle = screen.getByRole('separator')
  const capture = vi.fn()
  Object.defineProperty(handle, 'setPointerCapture', { value: capture })
  fireEvent.pointerDown(handle, { pointerId: 1, button: 0, clientX: 800 })
  expect(capture).toHaveBeenCalledWith(1)
  expect(document.body.style.cursor).toBe('col-resize')
  fireEvent.pointerMove(handle, { pointerId: 1, clientX: 650 })
  expect(handle).toHaveAttribute('aria-valuenow', '470')
  fireEvent.pointerUp(handle, { pointerId: 1 })
  expect(window.localStorage.getItem(storageKey)).toBe('470')
  expect(document.body.style.cursor).not.toBe('col-resize')
  fireEvent.pointerDown(handle, { pointerId: 2, button: 0, clientX: 650 })
  result.unmount()
  expect(document.body.style.cursor).not.toBe('col-resize')
  expect(document.body.style.userSelect).not.toBe('none')
})
