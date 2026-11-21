/**
 * What the liquid post-processing panels are looking at.
 *
 * Two halves, kept apart on purpose:
 *
 *   - the APP selection (a model, narrowed to a server or a fitting), owned by
 *     App and shared with the viewport and the tree. Picking a row selects the
 *     object it belongs to, and selecting an object anywhere lights its rows;
 *     `object_bindings` translates between the two (focusEdgeIdsFor).
 *   - the focused EDGE, held here: a pipe-segment model owns dozens of edges,
 *     so selecting "the supply ring" cannot say which of its 29 segments the
 *     user clicked. The right column's details read it to show that one edge.
 *
 * Module state rather than React state because the two panels are in
 * different columns; it never goes into the project.
 */

import { useEffect, useMemo, useState } from 'react'
import type { TreeSelection } from '../interaction/treeNodes'
import * as liquid from './liquidSession'
import { focusEdgeIdsFor } from './liquidNetwork'

let focusedEdge: string | null = null
const listeners = new Set<() => void>()

export function focusedLiquidEdge(): string | null {
  return focusedEdge
}

export function setFocusedLiquidEdge(edgeId: string | null): void {
  if (edgeId === focusedEdge) return
  focusedEdge = edgeId
  listeners.forEach((fn) => fn())
}

export function subscribeLiquidFocus(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function useFocusedLiquidEdge(): string | null {
  const [, bump] = useState(0)
  useEffect(() => subscribeLiquidFocus(() => bump((n) => n + 1)), [])
  return focusedEdge
}

/** The result document currently shown, re-rendering only when it is replaced. */
export function useLiquidResultDocument(): unknown {
  const [doc, setDoc] = useState(() => liquid.liquidRunState().results)
  useEffect(() => liquid.subscribeLiquid(() => {
    const next = liquid.liquidRunState().results
    setDoc((prev: unknown) => (prev === next ? prev : next))
  }), [])
  return doc
}

/**
 * The edges behind the app's selection, for the post-processing panel's
 * `focusEdgeIds`. Re-derived when the selection or the shown result changes.
 */
export function useLiquidFocusEdgeIds(uids: readonly string[], node: TreeSelection | null, enabled: boolean): string[] | undefined {
  const doc = useLiquidResultDocument()
  return useMemo(
    () => (enabled && uids.length ? focusEdgeIdsFor(doc, uids, node) : undefined),
    [enabled, doc, uids, node],
  )
}
