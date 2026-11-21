import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { KeyframeBadge } from '../transient/KeyframeBadge'

/**
 * What a plain box IS to the solver: a wall, or a heat source.
 *
 * A box with no role is just an obstruction. Give it `heatSource` and it
 * becomes a load — stated either as a total power spread over its surface, or
 * as a fixed surface temperature. The heat FLUX is shown alongside because
 * that is the number the boundary condition actually carries, and 2 kW on a
 * 0.2 m² box is a very different surface from 2 kW on a 6 m² one.
 *
 * Ported from `renderBoxCfdInspector` (STL_generate.html:8764).
 */

export interface BoxCfdSectionProps {
  model: Construction
}

/** Outer surface area of the box, for the flux read-out. */
export function boxSurfaceArea(size: { x?: number; y?: number; z?: number } | undefined): number {
  const x = Number(size?.x ?? 0)
  const y = Number(size?.y ?? 0)
  const z = Number(size?.z ?? 0)
  return 2 * (x * y + y * z + x * z)
}

const BOX_KINDS = new Set(['box', 'heat_source', 'heatSource', 'crh'])

export function BoxCfdSection({ model }: BoxCfdSectionProps) {
  const { t } = useTranslation()
  if (!BOX_KINDS.has(String(model.kind))) return null

  const cooling = ((model as unknown as { cooling?: Record<string, unknown> }).cooling ?? {}) as
    Record<string, unknown>
  const role = String(cooling.role ?? 'wall')
  const heatMode = String(cooling.heatMode ?? 'power')
  const power = Number(cooling.totalPower ?? 100)
  const temperature = Number(cooling.fixedTemperature ?? 35)
  const area = boxSurfaceArea((model.geometry as { size?: { x: number; y: number; z: number } })?.size)
  const flux = area > 1e-8 ? power / area : 0

  const set = (field: string, value: unknown) => store.setModelCooling(model.uid, field, value)

  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('inspector.box_cfd_role')}
      </div>
      <div className="prop-row">
        <label htmlFor="box-cfd-role">{t('label.cfd_role')}</label>
        <select id="box-cfd-role" value={role} onChange={(e) => set('role', e.target.value)}>
          <option value="wall">{t('label.role_wall')}</option>
          <option value="heatSource">{t('label.role_heat_source')}</option>
        </select>
      </div>
      {role === 'wall' ? (
        <div className="prop-hint">{t('hint.box_wall_role')}</div>
      ) : (
        <div style={{ borderLeft: '2px solid #ff6b35', paddingLeft: 6, marginTop: 8 }}>
          <div className="prop-row">
            <label htmlFor="box-heat-mode">{t('label.heat_mode')}</label>
            <select
              id="box-heat-mode"
              value={heatMode}
              onChange={(e) => set('heatMode', e.target.value)}
            >
              <option value="power">{t('label.heat_mode_power')}</option>
              <option value="temperature">{t('label.heat_mode_temperature')}</option>
            </select>
          </div>
          {heatMode === 'power' ? (
            <>
              <div className="prop-row kf-field">
                <label>{t('label.total_power')}<KeyframeBadge modelUid={model.uid} param="box.heat" target="" /></label>
                <NumberField
                  id="box-total-power"
                  value={power}
                  step={10}
                  min={0}
                  onCommit={(v) => set('totalPower', v)}
                />
              </div>
              <div className="prop-row">
                <label>{t('label.surface_area')}</label>
                <span className="value" id="box-surface-area">
                  {area.toFixed(4)} m²
                </span>
              </div>
              <div className="prop-row">
                <label>{t('label.heat_flux')}</label>
                <span className="value" id="box-heat-flux" style={{ color: '#ff6b35' }}>
                  {flux.toFixed(1)} W/m²
                </span>
              </div>
            </>
          ) : (
            <div className="prop-row">
              <label>{t('label.temperature_c')}</label>
              <NumberField
                id="box-fixed-temp"
                value={temperature}
                step={0.5}
                onCommit={(v) => set('fixedTemperature', v)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
