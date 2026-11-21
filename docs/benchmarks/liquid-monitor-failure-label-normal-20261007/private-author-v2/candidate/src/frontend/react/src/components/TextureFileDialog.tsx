import { useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { FileBrowser } from './FileBrowser'
import { useTranslation } from '../useTranslation'
import { fileToDataUrl, remoteImageToDataUrl, shrinkImageDataUrl } from '../geometry/faceTexture'

/**
 * Pick a picture for a face — the load-project dialog's two doors, reused.
 *
 * "Server file" walks the backend filesystem through the same FileBrowser the
 * case-directory picker uses (filtered to images); "Local file" opens the OS
 * picker, exactly as 加载工程 does for a .cfdproj on the user's own disk. Either
 * way the result is a data URL, downscaled so the project file stays small.
 */
export interface TextureFileDialogProps {
  startPath?: string
  onPicked: (dataUrl: string, sourceName: string) => void
  onClose: () => void
}

const IMAGE_EXTENSIONS = '.png,.jpg,.jpeg,.webp,.gif,.bmp'

export function TextureFileDialog({ startPath, onPicked, onClose }: TextureFileDialogProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<'remote' | 'local'>('remote')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const finish = async (load: () => Promise<string>, name: string) => {
    setBusy(true)
    setError(null)
    try {
      const raw = await load()
      const small = await shrinkImageDataUrl(raw)
      onPicked(small, name)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (tab === 'remote') {
    return (
      <FileBrowser
        extensions={IMAGE_EXTENSIONS}
        startPath={startPath}
        title={t('title.load_texture')}
        confirmLabel={busy ? t('filebrowser.loading') : t('btn.load_texture_confirm')}
        onPick={(path) => void finish(() => remoteImageToDataUrl(path), path.split('/').pop() ?? path)}
        onCancel={onClose}
        // The local tab sits under the browser, in the same dialog frame.
        footer={
          <div className="subtab-bar" style={{ marginTop: 6 }}>
            <button className="subtab lp-tab-btn active" data-tab="remote">{t('loadproj.tab_remote')}</button>
            <button className="subtab lp-tab-btn" data-tab="local" onClick={() => setTab('local')}>
              {t('loadproj.tab_local')}
            </button>
            {error && <span className="error" style={{ marginLeft: 8 }}>{error}</span>}
          </div>
        }
      />
    )
  }

  return createPortal(
    <div id="texture-file-dialog" className="modal-backdrop" onClick={onClose}>
      <div className="modal file-browser lp-dialog-inner" onClick={(e) => e.stopPropagation()}>
        <div className="header">
          <span className="title">{t('title.load_texture')}</span>
          <button className="node-action-btn" onClick={onClose} title={t('filebrowser.cancel')}>✕</button>
        </div>
        <div className="subtab-bar">
          <button className="subtab lp-tab-btn" data-tab="remote" onClick={() => setTab('remote')}>
            {t('loadproj.tab_remote')}
          </button>
          <button className="subtab lp-tab-btn active" data-tab="local">{t('loadproj.tab_local')}</button>
        </div>
        <div className="lp-body" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flex: '1 1 auto', gap: 16, padding: 24 }}>
          <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)', textAlign: 'center' }}>{t('hint.load_texture_local')}</div>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            id="file-texture"
            style={{ display: 'none' }}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void finish(() => fileToDataUrl(f), f.name)
              e.target.value = ''
            }}
          />
          <button className="btn primary lp-local-pick" style={{ width: 'auto', padding: '10px 24px' }} disabled={busy} onClick={() => fileInput.current?.click()}>
            {t('loadproj.local_btn')}
          </button>
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    </div>,
    document.body,
  )
}
