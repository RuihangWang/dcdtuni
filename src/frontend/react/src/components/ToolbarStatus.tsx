import { useSyncExternalStore } from 'react'
import { isLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'
import { countModels } from '../schema/cfdproj'
import * as store from '../projectStore'
import * as session from '../run/session'
import { animateViewPreset, type ViewPreset } from '../viewportBus'
import { Icon, type IconName } from './Icon'
import { useTranslation } from '../useTranslation'
import type { TranslationKey } from '../i18n'

/** The top bar's middle: the open project and where its case stands (mesh, results, the running job). */
export function ToolbarStatus() {
  const { t } = useTranslation()
  const project = useSyncExternalStore(store.subscribe, store.getProject)
  useSyncExternalStore(session.subscribeSession, () => session.getCaseStatus())
  const liquid = useSyncExternalStore(subscribeLiquidMode, isLiquidModeActive)
  const job = session.getCurrentCaseJob()
  if (!project) return <div className="toolbar-status" id="toolbar-status"><span className="ts-empty">{t('topbar.no_project')}</span></div>
  const models = countModels(project)
  const status = session.getCaseStatus()
  // Liquid mode counts its own runs; an air mesh means nothing there.
  const runs = (liquid ? status?.liquidRuns.length : status?.logRuns.length) ?? 0
  const mesh = session.isMeshPresent()
  return (
    <div className="toolbar-status" id="toolbar-status">
      <span className="ts-project" title={store.projectDisplayName()}>
        <Icon name="cube" />
        <b>{store.projectDisplayName()}</b>
        {store.isDirty() && <i className="ts-dirty" title={t('topbar.unsaved')} />}
        <small>{t('topbar.models', { n: models })}</small>
      </span>
      <span className="ts-sep" />
      {!liquid && (
        <span className={`ts-chip${mesh ? ' ok' : ''}`} data-state={mesh ? 'ok' : 'none'}>
          <Icon name="mesh" />{t('topbar.mesh')}<b>{mesh ? t('topbar.ready') : '—'}</b>
        </span>
      )}
      <span className={`ts-chip${runs ? ' ok' : ''}`} data-state={runs ? 'ok' : 'none'}>
        <Icon name="chart" />{t('topbar.results')}<b>{runs}</b>
      </span>
      {job?.running && (
        <span className="ts-chip busy" data-state="busy">
          <Icon name="refresh" className="spin" />{t(`topbar.job_${job.kind}` as TranslationKey)}
        </span>
      )}
    </div>
  )
}

const VIEWS: [ViewPreset, IconName, TranslationKey][] = [
  ['iso', 'cube', 'topbar.view_iso'],
  ['top', 'plan', 'topbar.view_top'],
  ['front', 'rack', 'topbar.view_front'],
  ['right', 'partition', 'topbar.view_side'],
]

/** One-click camera presets. */
export function ToolbarViews() {
  const { t } = useTranslation()
  return (
    <div className="toolbar-views" id="toolbar-views" role="group" aria-label={t('topbar.views')}>
      {VIEWS.map(([preset, icon, label]) => (
        <button key={preset} type="button" className="tool-btn" data-view={preset} title={t(label)}
          onClick={() => animateViewPreset(preset)}>
          <Icon name={icon} />{t(label)}
        </button>
      ))}
    </div>
  )
}
