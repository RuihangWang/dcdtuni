import { useEffect, useState } from 'react'
import type { TranslationKey } from '../i18n'
import * as session from '../run/session'
import * as store from '../projectStore'
import {
  getMeshWarnings,
  hasRejectedMeshQuality,
  installMeshWarningBridge,
  installMeshWarningFeed,
  subscribeMeshWarnings,
} from '../run/meshWarnings'
import { meshWarningLabel, meshWarningMessage } from '../run/meshWarningLabels'
import { useTranslation } from '../useTranslation'

/**
 * What checkMesh found, on the mesh tab: which checks failed and the
 * checker's own output.
 *
 * Mesh quality is reported, never required (owner 2026-10-22), and checking
 * the mesh belongs to the mesh tab alone (owner 2026-11-10): the solve tab
 * only asks whether the mesh still matches the geometry (`MeshChangeNote`).
 */

const WARN_COLOR: Record<number, string> = { 1: '#ff4444', 2: '#ff8c00', 3: '#ffcc00' }
const WARN_LEVEL_KEY: Record<number, TranslationKey> = {
  1: 'mesh_warn.level_critical',
  2: 'mesh_warn.level_warning',
  3: 'mesh_warn.level_info',
}

const LOG_STYLE = {
  marginTop: 8,
  maxHeight: 160,
  overflow: 'auto',
  fontSize: '0.68rem',
  lineHeight: 1.5,
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  padding: '6px 8px',
  color: 'var(--log-content)',
  userSelect: 'text',
  whiteSpace: 'pre-wrap',
} as const

export function MeshCheckDetail() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    // Idempotent: the feed fills the list from every mesh and checkMesh job.
    installMeshWarningFeed()
    installMeshWarningBridge()
  }, [])
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeMeshWarnings(() => bump((n) => n + 1)), [])
  useEffect(() => store.subscribeProjectLoad(() => setOpen(false)), [])

  const job = session.getCurrentCaseJob()
  const warnings = getMeshWarnings()
  const rejected = hasRejectedMeshQuality()

  return (
    <>
      {/* Which checks failed, not how many: non-orthogonality at 71° is a solve
          that will be slightly wrong, a tiny orphan region one that will not start. */}
      {warnings.length > 0 && (
        <div id="mesh-warn-detail" style={{ marginTop: 4 }}>
          <button
            className="btn"
            id="mesh-warn-toggle"
            onClick={() => setOpen((v) => !v)}
            style={{ fontSize: '0.72rem' }}
          >
            {`${open ? '▼' : '▶'} ${t(rejected ? 'mesh_warn.title_rejected' : 'mesh_warn.title_warning')} (${warnings.length})`}
          </button>
          {open && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
              {warnings.map((w, i) => (
                <div
                  key={`${w.code ?? 'warn'}-${i}`}
                  className="prop-hint"
                  style={{
                    textAlign: 'left',
                    padding: '4px 6px',
                    borderLeft: `2px solid ${WARN_COLOR[w.level] ?? '#ffcc00'}`,
                    background: 'var(--bg-input)',
                    lineHeight: 1.5,
                  }}
                >
                  <div style={{ color: WARN_COLOR[w.level] ?? '#ffcc00', fontFamily: "'Share Tech Mono',monospace" }}>
                    [{t(WARN_LEVEL_KEY[w.level] ?? 'mesh_warn.level_info')}] {meshWarningLabel(w)}
                  </div>
                  <div>{meshWarningMessage(w)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {/* The checker's own output: the numbers behind a warning (the actual
          max skewness) are read here. */}
      {job?.kind === 'checkMesh' && job.lines.length > 0 && (
        <pre id="check-mesh-log" style={LOG_STYLE}>{job.lines.slice(-200).join('\n')}</pre>
      )}
    </>
  )
}
