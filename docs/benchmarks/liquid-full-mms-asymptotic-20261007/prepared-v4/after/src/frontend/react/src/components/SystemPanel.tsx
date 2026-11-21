import { useEffect, useState } from 'react'
import * as session from '../run/session'
import { readCasePath } from '../run/caseDir'
import { FileBrowser } from './FileBrowser'
import { useTranslation } from '../useTranslation'

/**
 * The System tab: what the backend is, and the three admin flows.
 *
 * The upgrade flow has three outcomes, not two, and the third is the one that
 * matters: `git pull` can hit a CONFLICT, in which case the server has already
 * run `git merge --abort` and hands back the conflicting file list. Treating
 * that as a plain failure would leave a user staring at "upgrade failed" with no
 * idea which files to deal with, so the conflict list is rendered and the force
 * path (stash, then pull) is offered explicitly rather than silently taken.
 *
 * Restart is deliberately separate from upgrade even though the server asks for
 * it: `/api/admin/restart` re-execs the process, so pressing it is how a user
 * loses an in-flight mesh or solve. It stays a decision.
 */

interface UpgradeResult {
  ok: boolean
  status?: 'already_up_to_date' | 'updated' | 'conflict' | string
  restart?: boolean
  files?: string[]
  log?: string[]
  error?: string
}

export function SystemPanel() {
  // The folder browser, opened by 设置 case 目录.
  const [picking, setPicking] = useState(false)
  const [auth, setAuth] = useState<{ username?: string; role?: string } | null>(null)
  useEffect(() => {
    void fetch('/api/auth/check')
      .then((r) => r.json())
      .then((body) => setAuth(body as { username?: string; role?: string }))
      .catch(() => setAuth(null))
  }, [])
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [busy, setBusy] = useState<string | null>(null)
  const [result, setResult] = useState<UpgradeResult | null>(null)
  const [restartNote, setRestartNote] = useState<string | null>(null)

  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => {
    void session.refreshServerInfo()
  }, [])

  const info = session.getServerInfo()
  const caseDir = readCasePath() || info?.caseDir
  const caseDirOk = !!caseDir && info?.caseDir === caseDir && info.caseDirOk
  const status = session.getCaseStatus()
  const job = session.getJob()

  async function post(path: string): Promise<UpgradeResult> {
    const resp = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    try {
      return (await resp.json()) as UpgradeResult
    } catch {
      return { ok: resp.ok, error: `HTTP ${resp.status}` }
    }
  }

  async function upgrade(force = false) {
    setBusy(force ? 'force' : 'upgrade')
    setResult(null)
    try {
      setResult(await post(force ? '/api/admin/upgrade/force' : '/api/admin/upgrade'))
    } catch (err) {
      setResult({ ok: false, error: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(null)
    }
  }

  async function restart() {
    setBusy('restart')
    setRestartNote(t('status.restarting'))
    try {
      await post('/api/admin/restart')
    } catch {
      /* the server closes the connection as it re-execs; that is the success path */
    }
    // Poll until it answers again, then reload — the same shape as the HTML
    // editor's _waitForRestartAndReload.
    for (let i = 0; i < 60; i += 1) {
      await new Promise((r) => setTimeout(r, 2000))
      const info = await session.refreshServerInfo()
      if (info) {
        setRestartNote(t('status.restarted'))
        setBusy(null)
        location.reload()
        return
      }
    }
    setRestartNote(t('status.restart_timeout'))
    setBusy(null)
  }

  async function logout() {
    await post('/api/auth/logout')
    location.reload()
  }

  return (
    <div className="content" id="system-settings-panel">
      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          {t('title.server_model')}
        </div>
        <div className="prop-hint">{t('hint.single_user_server')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('title.server_status')}</div>
        <div className="prop-row">
          <label>DCTwin</label>
          <span className="value">
            {info ? `${info.dctwinAvailable ? '✅' : '❌'} ${info.dctwinVersion ?? ''}` : '—'}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('label.case_dir')}</label>
          {/* The full path, not just the leaf: two cases called `Base_leakage`
              under different parents are the common way to lose an afternoon. */}
          <span className="value" id="dctwin-case-display" title={caseDir}>
            {caseDir ? `${caseDirOk ? '✅' : '❌'} ${caseDir}` : t('inspector.dctwin_not_set')}
          </span>
        </div>
        <button
          className="btn primary"
          id="dctwin-setdir-btn"
          onClick={() => setPicking(true)}
        >
          {caseDirOk
            ? `✅ ${t('inspector.dctwin_ready_browse')}`
            : t('inspector.dctwin_set_case')}
        </button>
        {picking && (
          <FileBrowser
            pickDirectory
            startPath={caseDir || undefined}
            title={t('inspector.dctwin_set_case')}
            confirmLabel={t('btn.open')}
            onPick={(path) => {
              setPicking(false)
              void session.setCaseDir(path)
            }}
            onCancel={() => setPicking(false)}
          />
        )}
        <div className="prop-row">
          <label>CPU</label>
          <span className="value">{info?.cpuCount ?? '—'}</span>
        </div>
        <div className="prop-row">
          <label>{t('label.mesh_ready')}</label>
          <span className="value">{status?.meshReady ? '✅' : '—'}</span>
        </div>
        <div className="prop-row">
          <label>{t('label.solve_results')}</label>
          <span className="value">{status ? status.logRuns.length : '—'}</span>
        </div>
        {job && (
          <div className="prop-row">
            <label>{t('label.active_job')}</label>
            <span className="value">
              {job.kind} {job.running ? '⏳' : job.aborted ? '⏹' : job.returncode === 0 ? '✅' : '❌'}
            </span>
          </div>
        )}
        {/* Who the server thinks you are. On a shared host this is the
            difference between editing your own case and someone else's. */}
        <div className="prop-row" id="system-user-info">
          <label>{t('label.signed_in_as')}</label>
          <span className="value" id="system-username">
            {auth?.username ?? '—'}
            {auth?.role ? ` (${auth.role})` : ''}
          </span>
        </div>
        <button className="btn" id="system-refresh-btn" onClick={() => void session.refreshServerInfo()}>
          {t('btn.refresh')}
        </button>
      </div>

      <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
        <div className="prop-title" style={{ color: 'var(--accent3)' }}>
          {t('title.system_upgrade')}
        </div>
        <div className="prop-hint">{t('hint.upgrade_desc')}</div>
        <button
          className="btn"
          id="system-upgrade-btn"
          disabled={busy !== null}
          onClick={() => void upgrade(false)}
        >
          {busy === 'upgrade' ? t('status.upgrading') : t('btn.upgrade')}
        </button>

        {result && (
          <>
            <div
              className="prop-hint"
              style={{
                color: result.status === 'conflict' ? 'var(--hint-color)'
                     : result.ok ? 'var(--highlight)' : '#ff5c72',
              }}
            >
              {result.status === 'already_up_to_date' ? t('status.up_to_date')
                : result.status === 'updated' ? t('status.upgraded')
                : result.status === 'conflict' ? t('status.upgrade_conflict')
                : result.error ?? t('status.upgrade_failed')}
            </div>
            {result.status === 'conflict' && (
              <>
                <pre id="system-upgrade-conflict" style={LOG_STYLE}>
                  {(result.files ?? []).join('\n')}
                </pre>
                <div className="prop-hint">{t('hint.upgrade_force_warning')}</div>
                <button className="btn danger" disabled={busy !== null} onClick={() => void upgrade(true)}>
                  {busy === 'force' ? t('status.upgrading') : t('btn.upgrade_force')}
                </button>
              </>
            )}
            {result.restart && (
              <div className="prop-hint">{t('hint.upgrade_needs_restart')}</div>
            )}
            {result.log && result.log.length > 0 && (
              <pre id="system-upgrade-log" style={LOG_STYLE}>{result.log.join('\n')}</pre>
            )}
          </>
        )}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#ff5c72' }}>
        <div className="prop-title" style={{ color: '#ff5c72' }}>
          {t('title.server_control')}
        </div>
        <div className="prop-hint">{t('hint.restart_kills_jobs')}</div>
        <button
          className="btn danger"
          id="system-restart-btn"
          disabled={busy !== null}
          onClick={() => void restart()}
        >
          {busy === 'restart' ? t('status.restarting') : t('btn.restart_server')}
        </button>
        {restartNote && <div className="prop-hint">{restartNote}</div>}
        <button className="btn" id="system-logout-btn" disabled={busy !== null} onClick={() => void logout()}>
          {t('btn.logout')}
        </button>
      </div>
    </div>
  )
}

const LOG_STYLE: React.CSSProperties = {
  marginTop: 6,
  maxHeight: 180,
  overflow: 'auto',
  fontSize: '0.68rem',
  lineHeight: 1.5,
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  padding: '6px 8px',
  color: 'var(--log-content)',
  userSelect: 'text',
  whiteSpace: 'pre-wrap',
}
