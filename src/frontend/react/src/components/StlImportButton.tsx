import { useRef, useState } from 'react'
import { Icon } from './Icon'
import { importCustomStl } from '../io/stlAssets'
import { useTranslation } from '../useTranslation'

/**
 * Bring an external .stl into the project as a `custom` model.
 *
 * The legacy has the writer (`window.importCustomSTL`, STL_generate.html:15349)
 * but no longer has the input it reads — `document.getElementById('file-stl')`
 * matches nothing on the page, so the import is unreachable there. The id is
 * kept anyway: it is the one the legacy writer looks for, and the parity sweep
 * addresses controls by id.
 *
 * The input is hidden and driven by the button because a bare file input cannot
 * be styled to match the toolbar, and because it has to be RESET after every
 * pick — without clearing `value`, choosing the same file twice fires no change
 * event at all and the second import silently does nothing.
 */
export function StlImportButton({ onImported }: { onImported?: (uid: string) => void }) {
  const { t } = useTranslation()
  const inputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  async function pick(file: File | undefined) {
    if (!file) return
    setBusy(true)
    try {
      const result = await importCustomStl(file)
      if (result.ok && result.uid) onImported?.(result.uid)
      else window.alert(t('error.stl_import_failed', { error: result.error ?? '' }))
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <>
      <input
        id="file-stl"
        ref={inputRef}
        type="file"
        accept=".stl"
        style={{ display: 'none' }}
        onChange={(e) => void pick(e.target.files?.[0])}
      />
      <button
        className="btn"
        id="btn-import-stl"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
      >
        <Icon name="import" />
        {t('btn.import_stl')}
      </button>
    </>
  )
}
