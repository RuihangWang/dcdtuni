import type { Construction } from '../schema/cfdproj'
import { stlNaming } from '../legacy/stlNaming'
import * as store from '../projectStore'
import { setSlotDeleted } from '../geometry/rackSlots'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The rack's own CFD sections: its rear-door heat exchanger and its leakage.
 *
 * **RDHx** is a water-cooled door on the back of the rack. It is not geometry —
 * the runner models it as an effectiveness applied to the air leaving that
 * rack — so it belongs to the rack, not to any face, and a rack that has one
 * rejects a very different amount of heat into the room.
 *
 * **Leakage** is the read-out for the through-slots: how many U are open all
 * the way through, in how many groups, and with what σ. It is a summary rather
 * than an editor because a zone's σ belongs to the zone, which is edited by
 * clicking it — but a rack with no leakage at all is worth saying plainly,
 * since that is the usual reason a leakage case does not behave.
 *
 * Ported from `renderRackRDHxInspector` / `renderRackCfdInspector`
 * (STL_generate.html:9432, 9455).
 */

export interface RackCfdSectionProps {
  model: Construction
}

interface Rdhx {
  enabled?: boolean
  waterFlowRate?: number
  waterInletTemp?: number
  effectiveness?: number
}

const RDHX_DEFAULTS: Required<Rdhx> = {
  enabled: false,
  waterFlowRate: 20,
  waterInletTemp: 25,
  effectiveness: 0.5,
}

export function RackCfdSection({ model }: RackCfdSectionProps) {
  const { t } = useTranslation()
  if (model.kind !== 'rack') return null

  const rdhx = {
    ...RDHX_DEFAULTS,
    ...(((model.constructions as { rdhx?: Rdhx })?.rdhx ?? {}) as Rdhx),
  }

  function set(field: keyof Rdhx, value: unknown) {
    store.edit('setRackRDHx', `${model.uid}.${field}=${String(value)}`, (draft) => {
      const target = store.findConstruction(draft, model.uid)?.model
      if (!target) return
      const cons = ((target as unknown as { constructions?: Record<string, unknown> })
        .constructions ?? ((target as unknown as { constructions: Record<string, unknown> }).constructions = {})) as Record<string, unknown>
      const bag = (cons.rdhx ?? (cons.rdhx = { ...RDHX_DEFAULTS })) as Record<string, unknown>
      bag[field] = value
    })
  }

  const groups = (() => {
    try {
      const naming = stlNaming() as unknown as {
        getRackThroughZoneGroups?: (
          m: Construction,
        ) => { startUnit: number; endUnit: number; sigma?: number }[]
      }
      return naming.getRackThroughZoneGroups?.(model) ?? []
    } catch {
      return []
    }
  })()
  const throughSlots = groups.reduce((n, g) => n + (g.endUnit - g.startUnit + 1), 0)

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('rack.rdhx_title')}
        </div>
        <div className="prop-row">
          <label htmlFor="rdhx-enabled">{t('rack.enable_rdhx')}</label>
          <input
            id="rdhx-enabled"
            type="checkbox"
            checked={rdhx.enabled === true}
            onChange={(e) => set('enabled', e.target.checked)}
          />
        </div>
        {rdhx.enabled && (
          <>
            <div className="prop-row">
              <label>{t('rack.water_flow')} (LPM)</label>
              <NumberField
                id="rdhx-flow"
                value={Number(rdhx.waterFlowRate)}
                step={1}
                min={0}
                onCommit={(v) => set('waterFlowRate', v)}
              />
            </div>
            <div className="prop-row">
              <label>{t('rack.water_inlet_temp')} (°C)</label>
              <NumberField
                id="rdhx-inlet-temp"
                value={Number(rdhx.waterInletTemp)}
                step={0.5}
                onCommit={(v) => set('waterInletTemp', v)}
              />
            </div>
            <div className="prop-row">
              <label>{t('rack.effectiveness')}</label>
              <NumberField
                id="rdhx-effectiveness"
                value={Number(rdhx.effectiveness)}
                step={0.05}
                min={0}
                max={1}
                onCommit={(v) => set('effectiveness', v)}
              />
            </div>
          </>
        )}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {t('inspector.rack_cfd_leakage')}
        </div>
        {groups.length ? (
          <>
            <div className="prop-hint" id="rack-leakage-summary" style={{ color: '#e5a020' }}>
              {t('hint.rack_leakage_summary', {
                groups: groups.length,
                slots: throughSlots,
                summary: groups
                  .map((g) => `U${g.startUnit + 1}~U${g.endUnit + 1} σ=${g.sigma ?? 0.05}`)
                  .join(', '),
              })}
            </div>
            <div className="prop-hint">{t('hint.rack_leakage_click')}</div>
            <button
              className="btn"
              id="rack-sync-delete-all"
              onClick={() => {
                // Open the opposite face wherever one side is open: that is
                // what turns a set of one-sided gaps into leakage zones.
                const faces = (model as unknown as {
                  deletedFaces?: Record<string, Record<number, boolean>>
                }).deletedFaces ?? {}
                const slots = Number((model.geometry as { slot?: number })?.slot ?? 42)
                for (let i = 0; i < slots; i++) {
                  const front = faces.front?.[i] === true
                  const back = faces.back?.[i] === true
                  if (front !== back) setSlotDeleted(model.uid, front ? 'back' : 'front', i, true)
                }
              }}
            >
              {t('btn.sync_delete_all')}
            </button>
          </>
        ) : (
          <div className="prop-hint" id="rack-leakage-summary">
            {t('hint.rack_no_leakage')}
          </div>
        )}
      </div>
    </>
  )
}
