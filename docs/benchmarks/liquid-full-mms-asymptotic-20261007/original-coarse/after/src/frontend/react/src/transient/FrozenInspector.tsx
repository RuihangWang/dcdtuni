/**
 * The inspector off t = 0 in a transient project (transient design §2).
 *
 * A banner says what still edits — the tracked fields, marked ◆ ◐ ◇, whose edit
 * writes a keyframe at the playhead — and leads back to the start. Static controls are disabled for both pointer and keyboard input;
 * the store refuses those edits regardless (T2a), and the reason of the last
 * refused edit is shown here for a few seconds.
 */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { refusalText } from './refusalText'

const NOTICE_MS = 5000

export function FrozenInspector({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  useSyncExternalStore(store.subscribe, store.getProject)
  const playhead = useSyncExternalStore(store.subscribePlayhead, store.playheadTime)
  const seq = useSyncExternalStore(store.subscribe, store.rejectionSeq)
  const seen = useRef(seq)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    if (seq === seen.current) return
    seen.current = seq
    const why = store.lastEditRejection()
    if (!why) return
    setNotice(t('transient.refused', { reason: refusalText(why.reason) }))
    const timer = window.setTimeout(() => setNotice(null), NOTICE_MS)
    return () => window.clearTimeout(timer)
  }, [seq, t])

  const frozen = store.sceneFrozen()
  const form = useRef<HTMLDivElement>(null)
  // Keep each control's existing disabled state. This wrapper owns only the
  // additional timeline lock; returning to t=0 must not enable an independently
  // disabled field. The allowlist is the same one used by the inspector styles.
  const locked = useRef(new Map<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement, boolean>())
  const syncControls = () => {
    const container = form.current
    if (!container) return
    for (const [control, wasDisabled] of locked.current) {
      if (!frozen || !container.contains(control) || control.closest('.kf-field, .frozen-ok')) {
        control.disabled = wasDisabled
        locked.current.delete(control)
      }
    }
    if (!frozen) return
    for (const control of container.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement | HTMLButtonElement>('input, select, textarea, button')) {
      if (control.closest('.kf-field, .frozen-ok')) continue
      if (!locked.current.has(control)) {
        locked.current.set(control, control.disabled)
        // An uncontrolled name may still contain an uncommitted draft when a
        // user seeks away from t=0. Restore React's committed default before
        // blur can try (and correctly fail) to commit that draft off the start.
        if (control instanceof HTMLInputElement) {
          if (control.type === 'checkbox' || control.type === 'radio') control.checked = control.defaultChecked
          else control.value = control.defaultValue
        } else if (control instanceof HTMLTextAreaElement) control.value = control.defaultValue
      }
      if (!control.disabled) control.disabled = true
      if (document.activeElement === control) control.blur()
    }
  }
  // Layout phase makes the lock effective before paint or keyboard interaction;
  // the observer also covers legacy inspector rows inserted without a render.
  useLayoutEffect(() => { syncControls() })
  useLayoutEffect(() => {
    const container = form.current
    if (!container || !frozen) return
    const observer = new MutationObserver(syncControls)
    observer.observe(container, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'disabled'] })
    return () => observer.disconnect()
  }, [frozen])
  useLayoutEffect(() => () => {
    for (const [control, wasDisabled] of locked.current) control.disabled = wasDisabled
    locked.current.clear()
  }, [])
  return (
    <div ref={form} className={frozen ? 'inspector-frozen' : undefined} style={{ display: 'contents' }}>
      {frozen && (
        <div className="frozen-banner">
          <span>{t('transient.frozen.banner', { t: playhead })}</span>
          <button className="tool-btn frozen-ok" onClick={() => store.setPlayhead(0)}>
            {t('transient.frozen.to_start')}
          </button>
        </div>
      )}
      {notice && <div className="frozen-notice">{notice}</div>}
      {children}
    </div>
  )
}
