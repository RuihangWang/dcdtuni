import { Icon, type IconName } from './Icon'
import { useTranslation } from '../useTranslation'

export interface ExtremeRow {
  id: string
  kind: 'surface' | 'slice' | 'volume'
  name: string
  field: string
  unit: string
  max: string
  min: string
}

const KIND_ICON: Record<ExtremeRow['kind'], IconName> = { surface: 'palette', slice: 'slice', volume: 'volume' }

/** The marked extremes of each drawn layer: its name, then the highest and lowest value side by side. */
export function ExtremesCard({ rows }: { rows: ExtremeRow[] }) {
  const { t } = useTranslation()
  if (!rows.length) return null
  return (
    <div className="extremes" id={rows.some((r) => r.kind === 'volume') ? 'pp-vol-extremes' : 'pp-extremes'}>
      {rows.map((r) => (
        <div className="extreme-row" key={r.id} data-extreme={r.id}>
          <div className="extreme-name"><Icon name={KIND_ICON[r.kind]} size={13} /><span>{r.name}</span></div>
          <div className="extreme-values">
            <span className="extreme extreme-max" data-extreme={`${r.id}:max`} title={t('pp.extreme_max')}>
              <Icon name="arrowUp" size={12} /><b>{r.max}</b><small>{r.unit}</small>
            </span>
            <span className="extreme extreme-min" data-extreme={`${r.id}:min`} title={t('pp.extreme_min')}>
              <Icon name="arrowDown" size={12} /><b>{r.min}</b><small>{r.unit}</small>
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}
