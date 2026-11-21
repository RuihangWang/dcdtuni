import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { Icon } from './Icon'
import type { CfdProject } from '../schema/cfdproj'
import { useTranslation } from '../useTranslation'
import {
  DEFAULT_SORT,
  formatMtime,
  nextSortState,
  visibleEntries,
  type BrowseEntry,
  type SortColumn,
  type SortState,
} from '../io/fileBrowserModel'
import * as files from '../io/projectFiles'

/**
 * 加载工程 / 保存工程 — the project file dialog, both tabs.
 *
 * The port had a server-side file browser and nothing else, so the app could
 * only open paths the BACKEND can see and could only write back over the file it
 * had read. On a shared host that is not where most users keep their work: the
 * legacy dialog (STL_generate.html:15025-15343) has always had a second tab that
 * reads a .cfdproj off the user's own disk and, in save mode, hands one back as
 * a download. Both directions are here.
 *
 * Structurally the legacy dialog, down to the `lp-*` class names, so a selector
 * written against one app finds the same control in the other.
 */

export interface ProjectFileDialogProps {
  mode: 'load' | 'save'
  project: CfdProject | null
  /** Where the remote tab opens; the server falls back to its home directory. */
  startPath?: string
  /** The file open in this tab; null identifies a project without a remote source. */
  currentProjectPath?: string | null
  /** A remote project was read. The raw parsed document — validation is the caller's. */
  onLoaded: (data: unknown, sourceName: string, remotePath: string) => void
  /** The project was written to this server path. */
  onSaved: (path: string) => void
  /** The project was downloaded under this filename. */
  onLocalSaved: (name: string) => void
  /** Open the OS file picker. The dialog closes first, exactly as `_lpLocalPick` does. */
  onPickLocalFile: () => void
  onClose: () => void
}

interface Listing {
  current: string
  parent: string | null
  dirs: BrowseEntry[]
  files: BrowseEntry[]
}

interface DirectoryResponse { data: Listing; generation: number }

export function ProjectFileDialog({
  mode,
  project,
  startPath,
  currentProjectPath,
  onLoaded,
  onSaved,
  onLocalSaved,
  onPickLocalFile,
  onClose,
}: ProjectFileDialogProps) {
  const { t } = useTranslation()
  const isSaveMode = mode === 'save'
  const currentRemoteProjectPath = currentProjectPath === undefined
    ? files.getCurrentRemoteProjectFile()
    : currentProjectPath ?? ''
  const [tab, setTab] = useState<'remote' | 'local'>('remote')
  const [listing, setListing] = useState<Listing | null>(null)
  const [loading, setLoading] = useState(true)
  const [showHidden, setShowHidden] = useState(false)
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT)
  const [pathBar, setPathBar] = useState(startPath ?? '')
  const [selectedEntry, setSelectedEntry] = useState<{ path: string; type: 'dir' | 'file' } | null>(
    null,
  )
  const [selectedFilePath, setSelectedFilePath] = useState(() =>
    isSaveMode ? files.getDefaultRemoteSavePath(project, startPath, currentRemoteProjectPath) : currentRemoteProjectPath,
  )
  const [localName, setLocalName] = useState(() => files.getSuggestedProjectFileName(project, currentRemoteProjectPath))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /**
   * The dialog runs one initial fetch, and only one.
   *
   * `browse` closes over the current path state, so listing it as an effect
   * dependency re-fetches the directory on every keystroke in the path bar —
   * and each of those resets the selection the user is in the middle of making.
   */
  const started = useRef(false)
  const directoryGeneration = useRef(0)
  const alive = useRef(false)
  const directoryIsCurrent = useCallback((generation: number) =>
    alive.current && generation === directoryGeneration.current, [])
  const invalidateDirectory = useCallback(() => {
    alive.current = false
    directoryGeneration.current++
  }, [])
  const closeDialog = useCallback(() => {
    invalidateDirectory()
    onClose()
  }, [invalidateDirectory, onClose])

  useEffect(() => {
    alive.current = true
    return () => {
      invalidateDirectory()
      // StrictMode replays setup after cleanup. Its second setup must launch a
      // fresh initial request rather than reuse the now-invalid first request.
      started.current = false
    }
  }, [invalidateDirectory])

  const fetchDir = useCallback(
    async (path: string, fallbackToHome = false): Promise<DirectoryResponse | null> => {
      if (!alive.current) return null
      const generation = ++directoryGeneration.current
      setLoading(true)
      setError(null)
      let requestedPath = path
      let mayFallback = fallbackToHome
      try {
        while (true) {
          const url = '/api/browse-dir?extensions=.cfdproj' +
            (requestedPath ? '&path=' + encodeURIComponent(requestedPath) : '')
          const resp = await fetch(url)
          const body = await resp.json()
          if (!directoryIsCurrent(generation)) return null
          if (body.error) {
            // A current remembered path may fall back to home. An obsolete
            // initial error must never launch a new request over later navigation.
            if (mayFallback) {
              mayFallback = false
              requestedPath = ''
              continue
            }
            setError(String(body.error))
            return null
          }
          setError(null)
          return { data: body as Listing, generation }
        }
      } catch (err) {
        if (directoryIsCurrent(generation)) {
          setError(t('filebrowser.error_loading', { error: err instanceof Error ? err.message : String(err) }))
        }
        return null
      } finally {
        // An older completion must not clear a newer request's loading state.
        if (directoryIsCurrent(generation)) setLoading(false)
      }
    },
    [directoryIsCurrent, t],
  )

  const navigate = useCallback(
    async (path: string) => {
      const response = await fetchDir(path)
      if (!response || !directoryIsCurrent(response.generation)) return
      const { data } = response
      setListing(data)
      setPathBar(data.current)
      setSelectedEntry(null)
      setSelectedFilePath(
        isSaveMode ? files.joinFsPath(data.current, files.getSuggestedProjectFileName(project, currentRemoteProjectPath)) : '',
      )
    },
    [currentRemoteProjectPath, directoryIsCurrent, fetchDir, isSaveMode, project],
  )

  useEffect(() => {
    if (started.current) return
    started.current = true
    void (async () => {
      const response = await fetchDir(startPath ?? '', true)
      if (!response || !directoryIsCurrent(response.generation)) return
      const { data } = response
      setListing(data)
      setPathBar(data.current)
      // Recomputed from the paths rather than read out of state: a functional
      // setState updater must be pure, and StrictMode runs it twice.
      const current = currentRemoteProjectPath
      const inThisDir = !!current && files.getPathDirname(current) === data.current
      if (isSaveMode) {
        const wanted = files.getDefaultRemoteSavePath(project, startPath, currentRemoteProjectPath)
        setSelectedFilePath(
          files.getPathDirname(wanted) === data.current
            ? wanted
            : files.getDefaultRemoteSavePath(project, data.current, currentRemoteProjectPath),
        )
        if (inThisDir) setSelectedEntry({ path: current, type: 'file' })
      } else {
        // The remembered file is only worth pre-selecting when the dialog opened
        // in ITS directory; otherwise the "selected" box would name a file that
        // is not in the list on screen.
        setSelectedFilePath(inThisDir ? current : '')
        if (inThisDir) setSelectedEntry({ path: current, type: 'file' })
      }
    })()
  }, [currentRemoteProjectPath, directoryIsCurrent, fetchDir, isSaveMode, project, startPath])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeDialog()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closeDialog])

  const selectRow = (entry: { path: string; type: 'dir' | 'file' }) => {
    setSelectedEntry(entry)
    if (isSaveMode) {
      setSelectedFilePath(
        entry.type === 'file'
          ? entry.path
          : files.joinFsPath(entry.path, files.getSuggestedProjectFileName(project, currentRemoteProjectPath)),
      )
    } else {
      setSelectedFilePath(entry.type === 'file' ? entry.path : '')
    }
  }

  const confirmLoad = useCallback(async () => {
    if (!selectedFilePath) {
      setError(t('loadproj.no_file_selected'))
      return
    }
    setBusy(true)
    try {
      const resp = await fetch('/api/read-file?path=' + encodeURIComponent(selectedFilePath))
      const result = await resp.json()
      if (!result.ok) {
        setError(t('loadproj.read_failed', { error: result.error ?? resp.status }))
        return
      }
      const data = JSON.parse(result.content)
      onLoaded(data, files.getPathBasename(selectedFilePath), selectedFilePath)
    } catch (err) {
      setError(t('loadproj.read_failed', { error: err instanceof Error ? err.message : String(err) }))
    } finally {
      setBusy(false)
    }
  }, [onLoaded, selectedFilePath, t])

  const confirmSave = useCallback(async () => {
    const target = files.normalizeRemoteSaveTarget(selectedFilePath, project, currentRemoteProjectPath)
    if (!target || !project) {
      setError(t('saveproj.no_path_selected'))
      return
    }
    setBusy(true)
    try {
      const content = files.serializeProjectState(project)
      let result = await files.writeProjectToRemote(target, content, false)
      if (result.exists) {
        // The server refuses to clobber on the first try and reports `exists`,
        // so this prompt is the only place an overwrite can be authorised —
        // a silent overwrite here would be a silent loss of someone's project.
        if (!window.confirm(t('saveproj.overwrite_confirm', { path: target }))) return
        result = await files.writeProjectToRemote(target, content, true)
      }
      if (!result.ok) {
        setError(t('saveproj.write_failed', { error: result.error ?? 'unknown error' }))
        return
      }
      const written = result.path || target
      files.rememberRemoteProjectPath(written)
      onSaved(written)
    } catch (err) {
      setError(t('saveproj.write_failed', { error: err instanceof Error ? err.message : String(err) }))
    } finally {
      setBusy(false)
    }
  }, [currentRemoteProjectPath, onSaved, project, selectedFilePath, t])

  const confirmLocalSave = () => {
    if (!project) return
    onLocalSaved(files.downloadProjectToLocal(project, localName))
  }

  const confirm = () => {
    void (isSaveMode ? confirmSave() : confirmLoad())
  }

  const rows = visibleEntries(listing?.dirs, listing?.files, showHidden, sort)

  const sortHeader = (col: SortColumn, label: string, extra: CSSProperties) => (
    <div
      className={`lp-col-hdr lp-col-hdr-${col}`}
      onClick={() => setSort((s) => nextSortState(s, col))}
      style={{ cursor: 'pointer', fontSize: '0.75rem', color: 'var(--highlight)', padding: '5px 10px', ...extra }}
    >
      {label}
      {sort.col === col && (
        <span style={{ fontSize: '0.7rem' }}> {sort.asc ? t('filebrowser.sort_asc') : t('filebrowser.sort_desc')}</span>
      )}
    </div>
  )

  return (
    <div id="project-file-dialog" className="modal-backdrop" onClick={closeDialog}>
      <div className="modal file-browser lp-dialog-inner" onClick={(e) => e.stopPropagation()}>
        <div className="header">
          <span className="title">{t(isSaveMode ? 'saveproj.title' : 'loadproj.title')}</span>
          <button className="node-action-btn" onClick={closeDialog} title={t('filebrowser.cancel')}>
            ✕
          </button>
        </div>

        <div className="subtab-bar">
          <button
            className={`subtab lp-tab-btn${tab === 'remote' ? ' active' : ''}`}
            data-tab="remote"
            onClick={() => setTab('remote')}
          >
            {t('loadproj.tab_remote')}
          </button>
          <button
            className={`subtab lp-tab-btn${tab === 'local' ? ' active' : ''}`}
            data-tab="local"
            onClick={() => setTab('local')}
          >
            {t('loadproj.tab_local')}
          </button>
        </div>

        {tab === 'local' ? (
          <div
            className="lp-body"
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              flex: '1 1 auto',
              gap: 16,
              padding: 24,
            }}
          >
            <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)', textAlign: 'center' }}>
              {t(isSaveMode ? 'saveproj.local_desc' : 'loadproj.local_desc')}
            </div>
            {isSaveMode ? (
              <>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%', maxWidth: 420 }}>
                  <label style={{ fontSize: '0.78rem', color: 'var(--text-dim)' }}>
                    {t('saveproj.local_name_label')}
                  </label>
                  <input
                    type="text"
                    className="lp-local-name"
                    value={localName}
                    spellCheck={false}
                    onChange={(e) => setLocalName(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && confirmLocalSave()}
                    style={{ fontSize: '0.82rem', padding: '8px 10px' }}
                  />
                </div>
                <button className="btn primary" style={{ width: 'auto', padding: '10px 24px' }} onClick={confirmLocalSave}>
                  {t('saveproj.local_btn')}
                </button>
              </>
            ) : (
              <button
                className="btn primary lp-local-pick"
                style={{ width: 'auto', padding: '10px 24px' }}
                onClick={() => { invalidateDirectory(); onPickLocalFile() }}
              >
                {t('loadproj.local_btn')}
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="fb-path lp-remote-toolbar">
              <button
                className="btn lp-up-btn"
                style={{ width: 'auto', margin: 0, padding: '4px 10px' }}
                disabled={!listing?.parent || loading}
                title={t('filebrowser.go_up')}
                onClick={() => listing?.parent && void navigate(listing.parent)}
              >
                <Icon name="arrowUp" />
              </button>
              <input
                type="text"
                className="lp-path-bar"
                value={pathBar}
                spellCheck={false}
                onChange={(e) => setPathBar(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && void navigate(pathBar)}
              />
              <label
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  fontSize: '0.72rem',
                  color: 'var(--text-dim)',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                }}
              >
                <input
                  type="checkbox"
                  className="lp-hidden-toggle"
                  checked={showHidden}
                  onChange={(e) => setShowHidden(e.target.checked)}
                />
                {t('filebrowser.show_hidden')}
              </label>
            </div>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                borderBottom: '1px solid var(--border)',
                userSelect: 'none',
                flex: '0 0 auto',
              }}
            >
              {sortHeader('name', t('filebrowser.col_name'), { flex: '1 1 auto', paddingLeft: 34 })}
              {sortHeader('date', t('filebrowser.col_date'), { width: 140, flex: '0 0 auto', textAlign: 'right' })}
            </div>

            <div className="fb-list lp-body">
              {loading && <div className="empty">{t('filebrowser.loading')}</div>}
              {!loading && !listing && <div className="error">{t('loadproj.server_unavailable')}</div>}
              {!loading && listing && rows.length === 0 && (
                <div className="empty">{t('filebrowser.empty')}</div>
              )}
              {!loading &&
                rows.map((entry) => (
                  <div
                    key={entry.path}
                    className={`fb-row lp-entry-row fb-${entry.type}${
                      selectedEntry?.path === entry.path ? ' selected' : ''
                    }`}
                    data-path={entry.path}
                    data-type={entry.type}
                    title={entry.path}
                    onClick={() => selectRow({ path: entry.path, type: entry.type })}
                    onDoubleClick={() => (entry.type === 'dir' ? void navigate(entry.path) : confirm())}
                  >
                    <span className="fb-icon"><Icon name={entry.type === 'dir' ? 'folder' : 'sheet'} /></span>
                    <span className="fb-name">{entry.name}</span>
                    <span className="fb-size" style={{ width: 140, textAlign: 'right' }}>
                      {formatMtime(entry.mtime)}
                    </span>
                  </div>
                ))}
            </div>

            <div className="fb-path" style={{ borderBottom: 'none', borderTop: '1px solid var(--border)' }}>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
                {t('filebrowser.selected')}:
              </span>
              <input
                type="text"
                className="lp-selected-path"
                value={selectedFilePath}
                spellCheck={false}
                readOnly={!isSaveMode}
                placeholder={isSaveMode ? t('saveproj.remote_placeholder') : undefined}
                onChange={(e) => {
                  setSelectedFilePath(e.target.value)
                  setSelectedEntry(null)
                }}
              />
            </div>

            <div className="fb-actions lp-bottom-bar">
              <span className="fb-selected" />
              <button className="btn" style={{ width: 'auto', margin: 0 }} onClick={closeDialog}>
                {t('filebrowser.cancel')}
              </button>
              <button
                className="btn primary lp-confirm-btn"
                style={{ width: 'auto', margin: 0 }}
                disabled={busy}
                onClick={confirm}
              >
                {t(isSaveMode ? 'saveproj.save_btn' : 'loadproj.load_btn')}
              </button>
            </div>
          </>
        )}

        {error && <div className="error">{error}</div>}
      </div>
    </div>
  )
}
