import { flowTotals, type CfdProject } from '../schema/cfdproj'
import { useTranslation } from '../useTranslation'

/**
 * ACU supply / return totals in the toolbar.
 *
 * Informational only — it never gates a solve. What it is for is catching an
 * unbalanced model before meshing: a room whose supply and return disagree by
 * more than 1% has a boundary condition someone forgot to set, and that is far
 * cheaper to notice here than in a diverged solve.
 *
 * The 1% threshold and the ◌ / ✓ / ✗ states are the legacy monitor's.
 */

function fmt(v: number): string {
  if (v <= 0) return '—'
  return v >= 100 ? v.toFixed(0) : v.toFixed(2)
}

export function FlowMonitor({ project }: { project: CfdProject | null }) {
  const { t } = useTranslation()
  const { supply, return: ret } = flowTotals(project)
  const peak = Math.max(supply, ret, 1e-6)

  let state: 'idle' | 'ok' | 'fail' = 'idle'
  let icon = '◌'
  let delta = '—'
  if (supply > 0 || ret > 0) {
    const mean = 0.5 * (supply + ret)
    const drift = mean > 0 ? Math.abs(supply - ret) / mean : 0
    const pct = (drift * 100).toFixed(drift < 0.01 ? 2 : 1)
    const sign = supply > ret ? '+' : supply < ret ? '−' : ''
    if (drift <= 0.01) {
      state = 'ok'
      icon = '✓'
      delta = `${sign || '±'}${pct}%`
    } else {
      state = 'fail'
      icon = '✗'
      delta = `${sign}${pct}%`
    }
  }

  return (
    <div
      id="flow-monitor"
      className="flow-monitor"
      title={`${t('toolbar.flow_supply')} ${supply.toFixed(3)} m³/s · ${t('toolbar.flow_return')} ${ret.toFixed(3)} m³/s`}
    >
      <div className="flow-gauge" data-role="supply">
        <div className="flow-label">{t('toolbar.flow_supply')}</div>
        <div className="flow-bar">
          <div className="flow-bar-fill" style={{ width: `${((supply / peak) * 100).toFixed(1)}%` }} />
        </div>
        <div className="flow-value">
          {/* The legacy ids, so one selector reads the same number in both. */}
          <span id="flow-supply-value">{fmt(supply)}</span>
          <span className="flow-unit">m³/s</span>
        </div>
      </div>
      <div className="flow-gauge" data-role="return">
        <div className="flow-label">{t('toolbar.flow_return')}</div>
        <div className="flow-bar">
          <div className="flow-bar-fill" style={{ width: `${((ret / peak) * 100).toFixed(1)}%` }} />
        </div>
        <div className="flow-value">
          <span id="flow-return-value">{fmt(ret)}</span>
          <span className="flow-unit">m³/s</span>
        </div>
      </div>
      <div className="flow-balance" id="flow-balance" data-state={state}>
        <div className="flow-balance-icon">{icon}</div>
        <div className="flow-balance-delta" id="flow-balance-delta">
          {delta}
        </div>
      </div>
    </div>
  )
}
