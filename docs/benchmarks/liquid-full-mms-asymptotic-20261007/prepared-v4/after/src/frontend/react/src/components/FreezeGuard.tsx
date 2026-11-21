import { GUARD_CLASS } from '../run/uiFreeze'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import * as session from '../run/session'

/**
 * The settings half of a run panel, inert while a job runs.
 *
 * Wraps everything the legacy freeze would disable, leaving the run block
 * outside it so the abort button stays live — `_freezeSkipIds`
 * (STL_generate.html:28267) keeps the same four controls usable.
 *
 * `inert` is set through a ref rather than a JSX prop: React 18's typings do
 * not carry the attribute, and the whole point of this wrapper is that the
 * attribute survives the re-render every streamed log line triggers.
 */
export function FreezeGuard({ children }: { children: ReactNode }) {
  const [, bump] = useState(0)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  const frozen = session.isUiFrozen()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (frozen) el.setAttribute('inert', '')
    else el.removeAttribute('inert')
  }, [frozen])

  // `display: contents` so the wrapper contributes no box of its own: the panel
  // is a scrolling flex column and a real block here would swallow its gaps.
  // The class is what the freeze stylesheet dims — its CHILDREN, since a
  // box-less element cannot carry opacity, and never the scrolling panel.
  return (
    <div ref={ref} className={GUARD_CLASS} style={{ display: 'contents' }}>
      {children}
    </div>
  )
}
