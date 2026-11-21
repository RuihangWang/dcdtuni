import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import {
  SHORTCUT_ACTIONS,
  captureBinding,
  findConflict,
  formatBindings,
  formatKey,
  getShortcutBindings,
  resetShortcutBindings,
  setShortcutBinding,
  shortcutAction,
  subscribeShortcuts,
  swapShortcutBinding,
  type ShortcutActionId,
} from '../interaction/shortcuts'
import { t as translate, type TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'

/**
 * The keyboard-shortcut dialog: see every binding, change the single keys.
 *
 * A table of action | key | Change. Change captures the NEXT key press — Esc
 * cancels the capture, a combination or a key the page already uses is
 * refused with a reason, and the capture keeps listening. A key another
 * action already owns is shown as a conflict, and nothing changes until the
 * user swaps the two or cancels. Every change takes effect and is stored at
 * once (`shortcuts.ts`), so the dialog has Reset and Close and no Apply.
 *
 * The keys are captured on `window` in the CAPTURE phase and stopped there: the
 * first listener any key event reaches, so the key being bound is never also
 * acted on as a command by the editor, the 2D plan or first-person mode.
 *
 * `.modal-overlay` / `.modal` are the export dialog's chrome, reused so a
 * second modal does not look like a different kind of thing — and
 * `.modal-overlay` is what `editorKeys` looks for, so every editor shortcut is
 * off while this is open. Portalled to `body`: `#toolbar` has a
 * backdrop-filter, which would make it the containing block of a fixed
 * overlay and clip the dialog to the bar.
 */

type Capture =
  | { state: 'idle' }
  | { state: 'listening'; id: ShortcutActionId; error: string | null }
  | { state: 'conflict'; id: ShortcutActionId; key: string; other: ShortcutActionId }

/** First-person mode's keys: listed, not rebindable (`first_person_mode.js`). */
const FPS_ROWS: readonly { keys: string; labelKey: TranslationKey }[] = [
  { keys: 'W / A / S / D', labelKey: 'shortcut.fps_move' },
  { keys: 'Shift', labelKey: 'shortcut.fps_sprint' },
  { keys: 'Space', labelKey: 'shortcut.fps_up' },
  { keys: 'C', labelKey: 'shortcut.fps_crouch' },
  { keys: 'F', labelKey: 'shortcut.fps_fly' },
  { keys: 'Alt', labelKey: 'shortcut.fps_cursor' },
  { keys: 'Esc', labelKey: 'shortcut.fps_exit' },
]

export function ShortcutSettingsDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const bindings = useSyncExternalStore(subscribeShortcuts, getShortcutBindings)
  const [capture, setCaptureState] = useState<Capture>({ state: 'idle' })

  // Read through refs by ONE listener, installed once: re-installing it on
  // every state change would leave a window where a key reaches nobody. The
  // ref is written with the state, so a second key before the re-render
  // already sees the first one's result.
  const captureRef = useRef(capture)
  const setCapture = (next: Capture) => {
    captureRef.current = next
    setCaptureState(next)
  }
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const current = captureRef.current
      if (current.state !== 'listening') {
        if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          // Esc backs out one level: a pending conflict first, then the dialog.
          if (current.state === 'conflict') setCapture({ state: 'idle' })
          else onCloseRef.current()
        }
        return
      }
      // Listening: this key is the dialog's, and nothing else may act on it.
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') {
        setCapture({ state: 'idle' })
        return
      }
      const result = captureBinding(e)
      if (result.kind === 'wait') return
      if (result.kind === 'error') {
        setCapture({
          ...current,
          error:
            result.reason === 'modifier'
              ? translate('shortcut.err_modifier')
              : translate('shortcut.err_reserved', { key: formatKey(result.key) }),
        })
        return
      }
      const other = findConflict(getShortcutBindings(), current.id, result.key)
      if (other) {
        setCapture({ state: 'conflict', id: current.id, key: result.key, other })
        return
      }
      setShortcutBinding(current.id, [result.key])
      setCapture({ state: 'idle' })
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  const listeningId = capture.state === 'listening' ? capture.id : null

  return createPortal(
    <div id="shortcut-settings-overlay" className="modal-overlay" onClick={onClose}>
      <div className="modal shortcut-settings-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{t('shortcut.title')}</div>
        <table className="shortcut-table" id="shortcut-table">
          <thead>
            <tr>
              <th>{t('shortcut.col_action')}</th>
              <th>{t('shortcut.col_key')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {SHORTCUT_ACTIONS.map((action) => {
              const keys = bindings[action.id] ?? []
              const listening = listeningId === action.id
              return (
                <tr
                  key={action.id}
                  id={`shortcut-row-${action.id}`}
                  data-action={action.id}
                  className={listening ? 'listening' : undefined}
                >
                  <td>{t(action.labelKey)}</td>
                  <td>
                    <span
                      className={`shortcut-key${listening ? ' listening' : ''}`}
                      id={`shortcut-key-${action.id}`}
                    >
                      {listening
                        ? t('shortcut.press_key')
                        : keys.length
                          ? formatBindings(keys)
                          : t('shortcut.unbound')}
                    </span>
                  </td>
                  <td className="shortcut-actions">
                    {action.rebindable ? (
                      <button
                        className="btn"
                        id={`shortcut-change-${action.id}`}
                        disabled={capture.state === 'conflict'}
                        onClick={(e) => {
                          // Focus off the button: a Space or Enter meant as the
                          // new key must not also press it again.
                          e.currentTarget.blur()
                          setCapture(
                            listening
                              ? { state: 'idle' }
                              : { state: 'listening', id: action.id, error: null },
                          )
                        }}
                      >
                        {t('shortcut.change')}
                      </button>
                    ) : (
                      <span className="prop-hint">{t('shortcut.fixed')}</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        {capture.state === 'listening' && capture.error && (
          <div className="error" id="shortcut-error">
            {capture.error}
          </div>
        )}

        {capture.state === 'conflict' && (
          <div className="shortcut-conflict" id="shortcut-conflict">
            <div className="error">
              {t('shortcut.conflict', {
                key: formatKey(capture.key),
                action: t(shortcutAction(capture.other).labelKey),
              })}
            </div>
            <div className="prop-hint">
              {t('shortcut.conflict_hint', { action: t(shortcutAction(capture.other).labelKey) })}
            </div>
            <div className="modal-buttons">
              <button
                className="btn"
                id="shortcut-conflict-cancel"
                onClick={() => setCapture({ state: 'idle' })}
              >
                {t('btn.cancel')}
              </button>
              <button
                className="btn primary"
                id="shortcut-swap"
                onClick={() => {
                  swapShortcutBinding(capture.id, capture.key, capture.other)
                  setCapture({ state: 'idle' })
                }}
              >
                {t('shortcut.swap')}
              </button>
            </div>
          </div>
        )}

        <div className="prop-hint shortcut-hint">{t('shortcut.hint')}</div>

        <div className="shortcut-subtitle">{t('shortcut.fps_title')}</div>
        <table className="shortcut-table" id="shortcut-fps-table">
          <tbody>
            {FPS_ROWS.map((row) => (
              <tr key={row.keys}>
                <td>{t(row.labelKey)}</td>
                <td>
                  <span className="shortcut-key">{row.keys}</span>
                </td>
                <td className="shortcut-actions">
                  <span className="prop-hint">{t('shortcut.fixed')}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="modal-buttons">
          <button
            className="btn"
            id="shortcut-reset"
            onClick={() => {
              resetShortcutBindings()
              setCapture({ state: 'idle' })
            }}
          >
            {t('shortcut.reset')}
          </button>
          <button className="btn primary" id="shortcut-close" onClick={onClose}>
            {t('shortcut.close')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
