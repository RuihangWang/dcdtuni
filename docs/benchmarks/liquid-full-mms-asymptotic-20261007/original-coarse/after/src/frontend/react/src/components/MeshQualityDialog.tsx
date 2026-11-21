import { createPortal } from 'react-dom'
import type { TranslationKey } from '../i18n'
import type { MeshWarning } from '../run/meshWarnings'
import { useTranslation } from '../useTranslation'
import { meshWarningLabel, meshWarningMessage } from '../run/meshWarningLabels'

/**
 * The confirmation a solve has to pass when checkMesh flagged the mesh.
 *
 * A solve is hours. A mesh with negative-volume cells or a tiny orphan region
 * does not converge, it diverges, and the only sign before this dialog existed
 * was the run failing much later. So the gate is here, at the press of 执行求解,
 * and it lists WHAT was found — "3 warnings" is not something a user can act on.
 *
 * Ported from `showMeshQualityWarningDialog` (STL_generate.html:28001), ids
 * included.
 *
 * Portalled to `document.body` because `#left-panel` carries a `backdrop-filter`,
 * which makes it the containing block for `position: fixed` descendants: rendered
 * in place, this full-screen overlay was clipped to a 329px column inside the
 * solver panel — present in the DOM, and invisible.
 */

const LEVEL_COLOR: Record<number, string> = { 1: '#ff4444', 2: '#ff8c00', 3: '#ffcc00' }
const LEVEL_KEY: Record<number, TranslationKey> = {
  1: 'mesh_warn.level_critical',
  2: 'mesh_warn.level_warning',
  3: 'mesh_warn.level_info',
}
const LEVEL_ICON: Record<number, string> = { 1: '⛔', 2: '⚠️', 3: 'ℹ️' }

export interface MeshQualityDialogProps {
  warnings: readonly MeshWarning[]
  rejected?: boolean
  onCancel: () => void
  onContinue: () => void
}

export function MeshQualityDialog({ warnings, rejected = false, onCancel, onContinue }: MeshQualityDialogProps) {
  const { t } = useTranslation()
  // A critical finding changes what the dialog RECOMMENDS: the continue button
  // stops being the primary action and says so.
  const hasCritical = rejected || warnings.some((w) => w.level === 1)

  return createPortal(
    <div
      id="mesh-quality-warning-dialog"
      // A modal: the editor's keys stand down behind it (`editorKeys`).
      data-modal-open=""
      role="dialog" aria-modal="true" aria-labelledby="mesh-quality-dialog-title"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        zIndex: 12000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20,
      }}
    >
      <div
        style={{
          width: 'min(760px,92vw)',
          maxHeight: 'min(78vh,760px)',
          overflow: 'hidden',
          borderRadius: 10,
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--file-browser-bg,#10151c)',
          border: '1px solid rgba(90,167,255,0.35)',
          boxShadow: '0 24px 60px rgba(0,0,0,0.45)',
        }}
      >
        <div
          id="mesh-quality-dialog-title"
          style={{
            padding: '14px 18px',
            borderBottom: '1px solid rgba(90,167,255,0.18)',
            fontFamily: "'Orbitron',monospace",
            fontSize: '1rem',
            color: hasCritical ? '#ff4444' : '#5aa7ff',
            letterSpacing: '0.04em',
          }}
        >
          {rejected ? t('mesh_warn.title_rejected') : hasCritical ? t('mesh_warn.title_critical') : t('mesh_warn.title_warning')}
        </div>
        <div
          style={{
            padding: '14px 18px 0',
            color: 'var(--text-dim)',
            fontSize: '0.84rem',
            lineHeight: 1.7,
          }}
        >
          {rejected ? t('mesh_warn.desc_rejected') : hasCritical ? t('mesh_warn.desc_critical') : t('mesh_warn.desc_warning')}
        </div>
        <div
          style={{
            padding: '14px 18px',
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          {warnings.map((w, i) => {
            const color = LEVEL_COLOR[w.level] ?? '#ffcc00'
            return (
              <div
                key={`${w.code ?? 'warn'}-${i}`}
                style={{
                  padding: '10px 12px',
                  border: `1px solid ${color}33`,
                  background: `${color}0a`,
                  fontSize: '0.82rem',
                  lineHeight: 1.65,
                }}
              >
                <div
                  style={{
                    color,
                    fontFamily: "'Share Tech Mono',monospace",
                    marginBottom: 4,
                  }}
                >
                  {LEVEL_ICON[w.level] ?? 'ℹ️'} [{t(LEVEL_KEY[w.level] ?? 'mesh_warn.level_info')}]{' '}
                  {meshWarningLabel(w)}
                </div>
                <div style={{ color: 'var(--text-dim)' }}>{meshWarningMessage(w)}</div>
              </div>
            )
          })}
        </div>
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 10,
            padding: '14px 18px',
            borderTop: '1px solid rgba(90,167,255,0.18)',
          }}
        >
          <button className="btn" id="mesh-warn-cancel-btn" onClick={onCancel}>
            {rejected ? t('mesh_warn.btn_return_repair') : t('mesh_warn.btn_cancel')}
          </button>
          {!rejected && <button
            className={`btn${hasCritical ? '' : ' primary'}`}
            id="mesh-warn-continue-btn"
            style={hasCritical ? { borderColor: 'rgba(255,68,68,0.4)', color: '#ff6666' } : undefined}
            onClick={onContinue}
          >
            {hasCritical ? t('mesh_warn.btn_continue_risky') : t('mesh_warn.btn_continue')}
          </button>}
        </div>
      </div>
    </div>,
    document.body,
  )
}
