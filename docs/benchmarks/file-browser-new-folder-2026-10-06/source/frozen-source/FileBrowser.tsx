import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from '../useTranslation'
import { DialogResizeHandles } from '../ui/DialogResizeHandles'
import {
  DEFAULT_SORT,
  formatMtime,
  nextSortState,
  visibleEntries,
  type SortColumn,
  type SortState,
} from '../io/fileBrowserModel'
import { createRemoteDirectory } from '../io/projectFiles'

/**
 * The server-side file browser, as the HTML editor's load dialog does it.
 *
 * A `window.prompt` for a path was a placeholder and a bad one: the user cannot
 * see what exists, cannot navigate, and has to know an absolute path on a
 * machine they are not sitting at. This walks `/api/browse-dir`, which is the
 * same endpoint the legacy dialog uses.
 *
 * Filtering is server-side through `extensions`, so a directory with thousands
 * of files does not have to cross the wire to show two .cfdproj.
 *
 * The three controls around the listing are the legacy folder browser's
 * (STL_generate.html:18838-18926) and each answers a question the bare list
 * cannot. Show-hidden reveals dot-directories, which is where a case that was
 * set up by a script tends to live. New Folder creates the case directory
 * in place, so choosing where a run will go does not mean leaving for a shell.
 * And the date column sorts newest-first: a case directory accumulates one
 * timestamped run per solve, and without the sort finding the latest means
 * reading every row.
 */

export interface DirEntry {
  name: string
  path: string
  hidden: boolean
  mtime: number
  size?: number
}

interface Listing {
  current: string
  parent: string | null
  dirs: DirEntry[]
  files: DirEntry[]
}

export interface FileBrowserProps {
  /** Server-side extension filter, e.g. '.cfdproj'. */
  extensions?: string
  /** Where to open. Omitted lets the server pick (the user's home). */
  startPath?: string
  title: string
  confirmLabel: string
  onPick: (path: string) => void
  onCancel: () => void
  /**
   * Pick a DIRECTORY rather than a file.
   *
   * The case directory is a folder, and there is nothing inside it to click:
   * the confirm button takes the folder the browser is CURRENTLY IN, which is
   * why the label changes with the path rather than with a selection.
   */
  pickDirectory?: boolean
  /** Extra content under the list — a host's own tab strip, say. */
  footer?: ReactNode
}

export function FileBrowser({
  extensions,
  startPath,
  title,
  confirmLabel,
  onPick,
  onCancel,
  pickDirectory,
  footer,
}: FileBrowserProps) {
  const { t } = useTranslation()
  const [listing, setListing] = useState<Listing | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [manual, setManual] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT)
  const [folderDraft, setFolderDraft] = useState<{ parent: string; name: string } | null>(null)
  const [folderError, setFolderError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const folderButtonRef = useRef<HTMLButtonElement>(null)
  const hadFolderDraft = useRef(false)
  const folderNameId = useId()
  const navigationBlocked = busy || folderDraft !== null || creating
  useEffect(() => {
    if (folderDraft) hadFolderDraft.current = true
    else if (hadFolderDraft.current && !navigationBlocked) {
      folderButtonRef.current?.focus()
      hadFolderDraft.current = false
    }
  }, [folderDraft, navigationBlocked])
  const dialogRef = useRef<HTMLDivElement>(null)
  const lastClickedPath = useRef<string | null>(null)
  // The directory the browser is IN. Held in a ref as well as derived below,
  // because the Enter handler is installed once and would otherwise close over
  // the first listing for ever.
  const cwd = listing?.current ?? null
  const cwdRef = useRef<string | null>(null)
  cwdRef.current = cwd

  const browse = useCallback(
    async (path?: string) => {
      setBusy(true)
      lastClickedPath.current = null
      setError(null)
      try {
        const qs = new URLSearchParams()
        if (path) qs.set('path', path)
        if (extensions) qs.set('extensions', extensions)
        const resp = await fetch(`/api/browse-dir?${qs}`)
        const body = await resp.json()
        if (body.error) throw new Error(body.error)
        setListing(body as Listing)
        setSelected(null)
        setManual((body as Listing).current)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [extensions],
  )

  useEffect(() => {
    void browse(startPath)
  }, [browse, startPath])

  // Esc closes, Enter opens the selection — a dialog that traps the keyboard is
  // worse than no dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (folderDraft) {
          e.preventDefault()
          if (!creatingRef.current) { setFolderDraft(null); setFolderError(null) }
        } else onCancel()
      }
      if (e.key === 'Enter') {
        if (navigationBlocked) return
        if (pickDirectory) {
          if (cwdRef.current) onPick(cwdRef.current)
        }
        else if (selected) onPick(selected)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, onPick, selected, pickDirectory, navigationBlocked, folderDraft])

  /** Inline naming works in hosts where native browser dialogs are unavailable. */
  const newFolder = useCallback(() => {
    const here = cwdRef.current
    if (!here || busy || creatingRef.current) return
    setFolderError(null)
    setFolderDraft({ parent: here, name: '' })
  }, [busy])

  const cancelFolder = () => {
    if (creatingRef.current) return
    setFolderDraft(null)
    setFolderError(null)
  }

  const createFolder = async () => {
    const name = folderDraft?.name.trim()
    if (!folderDraft || !name || creatingRef.current) return
    const here = folderDraft.parent
    creatingRef.current = true
    setCreating(true)
    setFolderError(null)
    try {
      const result = await createRemoteDirectory(`${here.replace(/\/+$/, '')}/${name}`)
      if (!result.ok) {
        setFolderError(result.error ?? t('filebrowser.create_failed'))
        return
      }
      setFolderDraft(null)
      await browse(here)
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : String(err))
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  const rows = visibleEntries(listing?.dirs, pickDirectory ? undefined : listing?.files, showHidden, sort)

  const sortHeader = (col: SortColumn, label: string, extra: CSSProperties) => (
    <div
      className={`fb-col-hdr fb-col-hdr-${col}`}
      onClick={() => setSort((s) => nextSortState(s, col))}
      style={{
        cursor: 'pointer',
        fontSize: '0.75rem',
        color: 'var(--highlight)',
        padding: '5px 10px',
        ...extra,
      }}
    >
      {label}
      {sort.col === col && (
        <span style={{ fontSize: '0.7rem' }}>
          {' '}
          {sort.asc ? t('filebrowser.sort_asc') : t('filebrowser.sort_desc')}
        </span>
      )}
    </div>
  )

  return createPortal(
    <div className="modal-backdrop" onClick={onCancel}>
      <div
        className="modal file-browser"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
        style={{ position: 'relative' }}
      >
        {/* Eight handles, as the legacy dialog has: a case directory is picked
            by reading a long path out of the list, and the CSS width is not
            enough for one (STL_generate.html:18934). */}
        <DialogResizeHandles targetRef={dialogRef} />
        <div className="header">
          <span className="title">{title}</span>
          <button className="node-action-btn" onClick={onCancel} title={t('common.cancel')}>
            ✕
          </button>
        </div>

        <div className="fb-path">
          <button
            className="btn"
            style={{ width: 'auto', margin: 0, padding: '4px 10px' }}
            disabled={!listing?.parent || navigationBlocked}
            onClick={() => listing?.parent && void browse(listing.parent)}
            title={t('filebrowser.go_up')}
          >
            ↑
          </button>
          <input
            type="text"
            value={manual}
            disabled={navigationBlocked}
            spellCheck={false}
            onChange={(e) => setManual(e.target.value)}
            onKeyDown={(e) => !navigationBlocked && e.key === 'Enter' && (e.stopPropagation(), void browse(manual))}
          />
          <button
            className="btn"
            style={{ width: 'auto', margin: 0, padding: '4px 10px' }}
            disabled={navigationBlocked}
            onClick={() => void browse(manual)}
          >
            {t('filebrowser.go')}
          </button>
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
              className="fb-hidden-toggle"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
            />
            {t('filebrowser.show_hidden')}
          </label>
        </div>

        {folderDraft && (
          <form
            aria-label={t('filebrowser.new_folder')}
            onSubmit={(e) => { e.preventDefault(); void createFolder() }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelFolder() }
            }}
            style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
              padding: '10px 12px', borderBottom: '1px solid var(--border)' }}
          >
            <label htmlFor={folderNameId} style={{ width: '100%', fontSize: '0.8rem' }}>
              {t('filebrowser.new_folder_prompt')}
            </label>
            <input
              id={folderNameId}
              autoFocus
              type="text"
              value={folderDraft.name}
              disabled={creating}
              spellCheck={false}
              onChange={(e) => setFolderDraft({ ...folderDraft, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); void createFolder() }
              }}
              style={{ flex: '1 1 180px', minWidth: 0 }}
            />
            <button type="submit" className="btn primary" style={{ width: 'auto', margin: 0 }}
              disabled={creating || !folderDraft.name.trim()}>
              {creating ? t('common.loading') : t('filebrowser.new_folder')}
            </button>
            <button type="button" className="btn" style={{ width: 'auto', margin: 0 }}
              disabled={creating} onClick={cancelFolder}>
              {t('common.cancel')}
            </button>
            {folderError && <div role="alert" className="error" style={{ width: '100%' }}>{folderError}</div>}
          </form>
        )}

        <div
          className="fb-col-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            borderBottom: '1px solid var(--border)',
            userSelect: 'none',
            flex: '0 0 auto',
          }}
        >
          {sortHeader('name', t('filebrowser.col_name'), { flex: '1 1 auto', paddingLeft: 34 })}
          {sortHeader('date', t('filebrowser.col_date'), {
            width: 140,
            flex: '0 0 auto',
            textAlign: 'right',
          })}
        </div>

        <div className="fb-list">
          {busy && <div className="empty">{t('filebrowser.loading')}</div>}
          {!busy && error && <div className="error">{error}</div>}
          {!busy && !error && rows.length === 0 && (
            <div className="empty">{t('filebrowser.empty')}</div>
          )}
          {!busy &&
            rows.map((entry) =>
              entry.type === 'dir' ? (
                <div
                  key={entry.path}
                  className="fb-row fb-dir"
                  data-path={entry.path}
                  onClick={(e) => {
                    // A fast listing refresh can put another row under click two.
                    if (e.detail > 1 || navigationBlocked) return
                    void browse(entry.path)
                  }}
                  title={entry.path}
                >
                  <span className="fb-icon">📁</span>
                  <span className="fb-name">{entry.name}</span>
                  <span className="fb-size" style={{ width: 140, textAlign: 'right' }}>
                    {formatMtime(entry.mtime)}
                  </span>
                </div>
              ) : (
                <div
                  key={entry.path}
                  className={`fb-row fb-file${selected === entry.path ? ' selected' : ''}`}
                  data-path={entry.path}
                  onClick={(e) => {
                    if (e.detail > 1 || pickDirectory || navigationBlocked) return
                    lastClickedPath.current = entry.path
                    setSelected(entry.path)
                  }}
                  onDoubleClick={() => {
                    if (!navigationBlocked && !pickDirectory && lastClickedPath.current === entry.path) onPick(entry.path)
                  }}
                  title={entry.path}
                >
                  <span className="fb-icon">📄</span>
                  <span className="fb-name">{entry.name}</span>
                  <span className="fb-size" style={{ width: 140, textAlign: 'right' }}>
                    {formatMtime(entry.mtime)}
                  </span>
                </div>
              ),
            )}
        </div>

        {footer}
        <div className="fb-actions">
          <button
            className="btn fb-new-folder"
            ref={folderButtonRef}
            style={{ width: 'auto', margin: 0 }}
            disabled={!cwd || navigationBlocked}
            onClick={newFolder}
          >
            {t('filebrowser.new_folder')}
          </button>
          <span className="fb-selected" title={(pickDirectory ? cwd : selected) ?? ''}>
            {(pickDirectory ? cwd : selected) ?? t('filebrowser.pick_a_file')}
          </span>
          <button className="btn" style={{ width: 'auto', margin: 0 }} onClick={onCancel}>
            {t('common.cancel')}
          </button>
          <button
            className="btn primary"
            style={{ width: 'auto', margin: 0 }}
            disabled={navigationBlocked || (pickDirectory ? !cwd : !selected)}
            onClick={() => {
              if (pickDirectory) {
                if (cwd) onPick(cwd)
              } else if (selected) onPick(selected)
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    // Through a portal: the panel this dialog opens FROM carries a
    // `backdrop-filter`, which makes it the containing block for
    // `position: fixed` — so the "full-screen" backdrop was the width of the
    // right panel (measured: 339x920 at x=1581) with the 760px dialog squeezed
    // to 420. The same trap `PpInitTask` and `SensorEditDialog` already hit.
    document.body,
  )
}
