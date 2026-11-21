import { useCallback, useEffect, useState } from 'react'
import { getMonitoringConfig, setMonitoringConfig, type MonitorPatch } from '../run/monitoring'
import { useTranslation } from '../useTranslation'

/**
 * The 求解监控 panel — what the solver writes probes for.
 *
 * Replicates `#solver-monitoring-right` (STL_generate.html:1052): the residual
 * curve toggle, the per-server temperature list and the ACU return list, each
 * with select-all / none.
 *
 * ## Why this is not cosmetic
 *
 * The choices here travel into the .cfdrun as `simulationConfig.monitoring`,
 * which is what the runner turns into OpenFOAM function objects. Without the
 * panel React exported `monitoring: {}` — a manifest that asks for no probes at
 * all, while the legacy one asks for residuals. The case still solves, and the
 * convergence plot is simply empty.
 *
 * The patch lists come from `/api/server-patches`, which reads the CASE's
 * boundary file: these are the names the solved mesh actually has, and a list
 * built from the project would offer patches that do not exist in it.
 */

interface ServerPatchResponse {
  patches?: MonitorPatch[]
  acuReturnPatches?: MonitorPatch[]
  error?: string
}

export function SolverMonitorPanel() {
  const { t } = useTranslation()
  // Seeded from the MODULE, not from the defaults. This panel is unmounted by
  // every tab switch — the 传感器位点 sub-tab included — and starting from an
  // empty Set meant twenty ticked server patches came back untouched-looking
  // and, worse, were republished as an empty config the moment the panel
  // mounted. The legacy keeps the checkboxes in static DOM that is only
  // hidden (STL_generate.html:1052), so its ticks simply survive.
  const saved = getMonitoringConfig()
  const [residuals, setResiduals] = useState(saved.residuals)
  const [servers, setServers] = useState<MonitorPatch[]>([])
  const [acus, setAcus] = useState<MonitorPatch[]>([])
  const [serverOn, setServerOn] = useState<Set<string>>(
    () => new Set(saved.serverTemperature.map((p) => p.patch)),
  )
  const [acuOn, setAcuOn] = useState<Set<string>>(
    () => new Set(saved.acuReturnTemperature.map((p) => p.patch)),
  )
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const resp = await fetch('/api/server-patches')
        const data = (await resp.json()) as ServerPatchResponse
        if (cancelled) return
        if (data.error) {
          setError(data.error)
        } else {
          setServers(data.patches ?? [])
          setAcus(data.acuReturnPatches ?? [])
        }
      } catch (e) {
        if (!cancelled) setError(`${t('sm.request_failed')}${e instanceof Error ? e.message : e}`)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [t])

  // Publish on every change rather than reading the DOM at export time, which
  // is what the legacy `smGetMonitoringConfig` does — a panel that is not
  // mounted (a different tab) would give an empty config there.
  const publish = useCallback(
    (r: boolean, s: Set<string>, a: Set<string>) => {
      setMonitoringConfig({
        residuals: r,
        serverTemperature: servers.filter((p) => s.has(p.patch)),
        acuReturnTemperature: acus.filter((p) => a.has(p.patch)),
      })
    },
    [servers, acus],
  )

  useEffect(() => {
    // NOT while the patch lists are still loading. `publish` filters the ticked
    // ids against `servers`/`acus`, which are empty until `/api/server-patches`
    // answers — so publishing here would write an empty selection over the one
    // this panel was just seeded with, and the ticks would be gone by the time
    // they were drawn.
    if (loading) return
    publish(residuals, serverOn, acuOn)
  }, [loading, publish, residuals, serverOn, acuOn])

  function toggle(set: Set<string>, key: string, on: boolean) {
    const next = new Set(set)
    if (on) next.add(key)
    else next.delete(key)
    return next
  }

  function list(
    patches: MonitorPatch[],
    on: Set<string>,
    setOn: (s: Set<string>) => void,
    className: string,
    suffix?: string,
    emptyKey: 'sm.no_server_boundaries' | 'sm.no_acu_return_faces' = 'sm.no_server_boundaries',
  ) {
    if (loading) return <div className="sm-empty">{t('common.loading')}</div>
    if (!patches.length) return <div className="sm-empty">{t(emptyKey)}</div>
    return patches.map((p) => (
      <div className="sm-row" key={p.patch}>
        <label title={p.patch}>
          <input
            type="checkbox"
            className={className}
            data-patch={p.patch}
            data-short={p.shortId}
            data-type={p.type}
            checked={on.has(p.patch)}
            onChange={(e) => setOn(toggle(on, p.patch, e.target.checked))}
          />{' '}
          <span className="sm-patch-caption">{p.shortId} ({suffix ?? p.type})</span>
        </label>
      </div>
    ))
  }

  return (
    <div id="solver-monitoring-right">
      <div className="header">{t('sm.header')}</div>

      <div className="sm-section">
        <label>
          <input
            type="checkbox"
            id="sm-residual-cb"
            checked={residuals}
            onChange={(e) => setResiduals(e.target.checked)}
          />{' '}
          {t('sm.monitor_residual')}
        </label>
      </div>

      <div className="sm-section">
        <div className="sm-section-head">
          <label>{t('sm.server_temp_monitor')}</label>
          <div className="sm-section-btns">
            <button id="sm-temp-all" onClick={() => setServerOn(new Set(servers.map((p) => p.patch)))}>
              {t('common.select_all')}
            </button>
            <button id="sm-temp-none" onClick={() => setServerOn(new Set())}>
              {t('common.deselect_all')}
            </button>
          </div>
        </div>
        <div id="sm-server-list">{list(servers, serverOn, setServerOn, 'sm-temp-cb')}</div>
      </div>

      <div className="sm-section">
        <div className="sm-section-head">
          <label style={{ color: '#7ecfff' }}>{t('sm.acu_return_monitor')}</label>
          <div className="sm-section-btns">
            <button id="sm-acu-all" onClick={() => setAcuOn(new Set(acus.map((p) => p.patch)))}>
              {t('common.select_all')}
            </button>
            <button id="sm-acu-none" onClick={() => setAcuOn(new Set())}>
              {t('common.deselect_all')}
            </button>
          </div>
        </div>
        <div id="sm-acu-return-list">
          {list(acus, acuOn, setAcuOn, 'sm-acu-return-cb', 'return', 'sm.no_acu_return_faces')}
        </div>
      </div>

      {error && <div className="sm-error">{error}</div>}
    </div>
  )
}
