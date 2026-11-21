import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from '../useTranslation'
import * as session from '../run/session'
import { FileBrowser } from './FileBrowser'
import { getRepoRoot } from '../run/caseDir'

/**
 * A project names its case directory (`meta.casePath`). Made on another
 * machine, or after the directory moved, the server finds it missing; a line
 * in the log was all the user got, and mesh/solve then failed for want of a
 * case. This asks for a directory instead: choose one now (the server's file
 * browser, opened at the repository's case/ folder), or later from
 * System > Set Case Directory.
 */
export function CasePathPrompt() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [picking, setPicking] = useState(false)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])

  const invalid = session.getInvalidCasePath()
  // A teammate's first project may fail before server info named the
  // repository; the browser should still open at its case/ folder.
  useEffect(() => {
    if (invalid !== null && !getRepoRoot()) void session.refreshServerInfo()
  }, [invalid])
  if (invalid === null) return null
  const root = getRepoRoot() ?? session.getServerInfo()?.repoRoot
  const startPath = root ? `${root}/case` : session.getServerInfo()?.caseDir || undefined

  if (picking) {
    return (
      <FileBrowser
        pickDirectory
        startPath={startPath}
        title={t('case_prompt.choose_title')}
        confirmLabel={t('btn.open')}
        onPick={(path) => {
          setPicking(false)
          void session.setCaseDir(path)
        }}
        onCancel={() => setPicking(false)}
      />
    )
  }

  return createPortal(
    <div
      id="case-path-prompt"
      data-modal-open=""
      role="dialog" aria-modal="true" aria-labelledby="case-path-prompt-title"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 12000,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      }}
    >
      <div
        style={{
          width: 'min(560px,92vw)', borderRadius: 10, display: 'flex', flexDirection: 'column',
          background: 'var(--file-browser-bg,#10151c)', border: '1px solid rgba(90,167,255,0.35)',
          boxShadow: '0 24px 60px rgba(0,0,0,0.45)',
        }}
      >
        <div
          id="case-path-prompt-title"
          style={{
            padding: '14px 18px', borderBottom: '1px solid rgba(90,167,255,0.18)',
            fontFamily: "'Orbitron',monospace", fontSize: '1rem', color: '#ffb347', letterSpacing: '0.04em',
          }}
        >
          {t('case_prompt.title')}
        </div>
        <div style={{ padding: '14px 18px', color: 'var(--text-dim)', fontSize: '0.84rem', lineHeight: 1.7 }}>
          <div>{t('case_prompt.desc')}</div>
          <code
            id="case-path-prompt-path"
            style={{ display: 'block', marginTop: 8, padding: '6px 8px', background: 'rgba(255,255,255,0.05)', overflowWrap: 'anywhere' }}
          >
            {invalid}
          </code>
          <div style={{ marginTop: 8 }}>{t('case_prompt.hint')}</div>
        </div>
        <div style={{ padding: '0 18px 16px', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" id="case-path-prompt-later" onClick={() => session.dismissInvalidCasePath()}>
            {t('case_prompt.later')}
          </button>
          <button className="btn primary" id="case-path-prompt-choose" onClick={() => setPicking(true)}>
            {t('case_prompt.choose')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
