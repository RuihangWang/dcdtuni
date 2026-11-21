import { useEffect, useState, useSyncExternalStore } from 'react'
import * as session from '../run/session'
import * as store from '../projectStore'
import { currentMeshChange, getMeshChange, meshBlockFor, subscribeMeshChange, watchMeshChange, type MeshBlock } from '../run/meshChange'
import { useTranslation } from '../useTranslation'
import { StatusMark } from './Icon'

/**
 * Whether the run buttons wait on the mesh: when running on it would fail, and while there is no verdict for the
 * current edit yet (a run started then would meet a mesh that may not fit). Read in the same render as the edit,
 * so the button never stays live for a frame after it.
 */
export function useMeshBlock(): MeshBlock {
  const live = useSyncExternalStore(subscribeMeshChange, getMeshChange)
  useSyncExternalStore(store.subscribe, store.documentInputRevision)
  return meshBlockFor(currentMeshChange(), live.error)
}

/**
 * Whether the mesh still fits the geometry: solve on it, update its patches, or generate it again.
 * Shown above the solve and AI solve buttons, which wait on `useMeshBlock`.
 */
export function MeshChangeNote() {
  const { t } = useTranslation()
  const live = useSyncExternalStore(subscribeMeshChange, getMeshChange)
  const [, bump] = useState(0)
  useEffect(() => watchMeshChange(), [])
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])
  const change = currentMeshChange()
  const running = !!session.getJob()?.running

  if (!change) {
    if (live.error) return <div className="mesh-change warn" id="mesh-change" data-kind="error">{t('meshchange.error', { e: live.error })}</div>
    return <div className="mesh-change" id="mesh-change" data-kind="checking"><StatusMark status="busy" /> {t('meshchange.checking')}</div>
  }
  const names = [...(change.added ?? []), ...(change.changed ?? [])]
  if (change.kind === 'none') {
    return <div className="mesh-change ok" id="mesh-change" data-kind="none"><StatusMark status="ok" /> {t('meshchange.none')}</div>
  }
  if (change.kind === 'patches') {
    return (
      <div className="mesh-change warn" id="mesh-change" data-kind="patches">
        <div><StatusMark status="warn" /> {change.reason === 'refresh'
          ? t('meshchange.refresh')
          : change.reason === 'retype'
            ? t('meshchange.retype', { n: Object.keys(change.retype ?? {}).length })
            : t('meshchange.patches', { n: names.length || (change.removed ?? []).length })}</div>
        {change.reason === 'retype' && <div className="mesh-change-names">{Object.keys(change.retype ?? {}).slice(0, 4).join(', ')}</div>}
        {names.length > 0 && <div className="mesh-change-names" title={names.join('\n')}>{names.slice(0, 4).join(', ')}{names.length > 4 ? ' …' : ''}</div>}
        <button type="button" className="btn" id="mesh-change-update" disabled={running}
          onClick={() => {
            store.logOperation('mesh', t('meshchange.update'))
            void session.runJob('mesh', undefined, { patchUpdate: true }).catch((err) =>
              store.logOperation('mesh', t('meshchange.error', { e: err instanceof Error ? err.message : String(err) })))
          }}>
          {t('meshchange.update')}
        </button>
        {!change.reason && <div className="prop-hint">{t('meshchange.patches_hint')}</div>}
      </div>
    )
  }
  return (
    <div className="mesh-change error" id="mesh-change" data-kind={change.kind}>
      <StatusMark status="error" /> {change.kind === 'mesh' ? t('meshchange.no_mesh')
        : change.reason === 'settings' ? t('meshchange.settings', { names: (change.settings ?? []).slice(0, 4).join(', ') })
          : t('meshchange.geometry')}
    </div>
  )
}
