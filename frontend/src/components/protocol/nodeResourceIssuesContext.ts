import { createContext, createElement, useContext, type ComponentType, type ReactNode } from 'react'
import type { ImportReadiness } from '@/types/packageImports'
import { WarningBadge } from './nodes/WarningBadge'

export const ResourceIssues = createContext<{ issues: ImportReadiness['issues']; inspector: ReactNode }>({ issues: [], inspector: null })

// One wrapper applies resource warnings to every registered node renderer.
export function withResourceWarnings<P extends { id: string }>(Renderer: ComponentType<P>) {
  return function ResourceNode(props: P) {
    const issues = useContext(ResourceIssues).issues.filter(issue => issue.node_id === props.id).map(issue => issue.message)
    return createElement('div', { className: 'relative' }, createElement(Renderer, props),
      issues.length ? createElement(WarningBadge, { issues, className: 'absolute -left-1 -top-1 z-10' }) : null)
  }
}
