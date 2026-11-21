import { createPortal } from 'react-dom'
import { useTranslation } from '../useTranslation'

/**
 * The blocking task dialog 开启后处理 runs behind.
 *
 * A port of `showBlockingTaskDialog` (STL_generate.html:23930) narrowed to the
 * one caller that needs it: a title, a log of the steps, and a cancel button.
 * The ids are the legacy's (`blocking-task-mask`, `blocking-task-dialog`) so one
 * selector finds the dialog in either app.
 *
 * ## Why this is not just a spinner
 *
 * The init is FOUR requests — native result loading, the patch table, a geometry
 * buffer, the first field — and on a large case it runs for the better part of
 * a minute. Without the step log the whole of that is one frozen button, and
 * without cancel the only way out of a run started on the wrong directory is to
 * reload the page and lose the session.
 *
 * The mask does not cover the viewport, matching the legacy's hole-punch: the
 * camera stays usable while the case loads.
 *
 * ## Through a portal, or it lands in the sidebar
 *
 * The post-processing panel sits inside a container with a backdrop filter, and
 * a `position: fixed` child of a filtered ancestor is positioned against THAT
 * ancestor rather than the viewport. Rendered in place, the dialog appeared
 * half off the left edge of the screen, centred on a 320 px panel.
 */

export interface PpInitTaskProps {
  title: string
  lines: string[]
  /** Fields the server reported as all-NaN. Shown apart from the log, in red. */
  warnings: string[]
  onCancel(): void
}

export function PpInitTask({ title, lines, warnings, onCancel }: PpInitTaskProps) {
  const { t } = useTranslation()
  return createPortal(
    <>
      <div
        id="blocking-task-mask"
        // A modal: the editor's keys stand down behind it (`editorKeys`).
        data-modal-open=""
        style={{ position: 'fixed', inset: 0, zIndex: 9998, background: 'rgba(0,0,0,0.15)' }}
      />
      <div
        id="blocking-task-dialog"
        style={{
          position: 'fixed',
          zIndex: 9999,
          top: '30%',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 420,
          maxWidth: '90vw',
          background: 'var(--bg-sidebar, #1a1f2e)',
          border: '1px solid var(--border, #444)',
          borderRadius: 8,
          boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
          color: 'var(--text, #ccc)',
          fontSize: '0.8rem',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div
          style={{
            padding: '10px 14px',
            borderBottom: '1px solid var(--border, #333)',
            fontWeight: 'bold',
            color: 'var(--accent3, #00d4ff)',
            fontSize: '0.85rem',
          }}
        >
          {title}
        </div>
        <div
          id="blocking-task-log"
          style={{
            padding: '8px 14px',
            maxHeight: 200,
            overflowY: 'auto',
            fontFamily: 'monospace',
            fontSize: '0.73rem',
            color: '#a0b0c0',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {lines.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
          {warnings.map((line, i) => (
            <div key={`w${i}`} className="pp-nan-warning" style={{ color: '#ffb454' }}>
              {line}
            </div>
          ))}
        </div>
        <div style={{ padding: '8px 14px 12px' }}>
          <button
            id="pp-init-cancel"
            onClick={onCancel}
            style={{
              width: '100%',
              padding: 8,
              background: '#5c2626',
              color: '#ff6b6b',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            {t('btn.cancel')}
          </button>
        </div>
      </div>
    </>,
    document.body,
  )
}
