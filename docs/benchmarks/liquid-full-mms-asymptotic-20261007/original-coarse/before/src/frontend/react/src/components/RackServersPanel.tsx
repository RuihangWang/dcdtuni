import { useState } from 'react'
import type { Construction } from '../schema/cfdproj'
import { addRackServerQuick, rackServerRows, rackSlotCount } from '../geometry/rackServers'
import { removeEquipment, type EquipmentKind } from '../geometry/rackSlots'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The rack's servers, and a row that adds one by typing where it goes.
 *
 * Without it a server can only be created by hitting its exact U-slot in the
 * 3D viewport — workable for a demo rack, not for a 42U cabinet read off a
 * datasheet, and impossible for a slot hidden behind a door.
 *
 * Ported from `_rackServerPanelHtml` / `rackServerPanelAdd`
 * (STL_generate.html:9310, 9360). The start unit is shown 1-BASED because that
 * is what a rack is labelled with; the store is 0-based, and the conversion
 * happens here rather than in `rackServers.ts` so the module keeps the legacy's
 * own convention.
 */

export interface RackServersPanelProps {
  model: Construction
}

const KIND_LABEL: Record<EquipmentKind, 'rack.servers.kind_fan' | 'rack.servers.kind_nonfan' | 'rack.servers.kind_liquid'> = {
  fanServer: 'rack.servers.kind_fan',
  nonFan: 'rack.servers.kind_nonfan',
  liquidServer: 'rack.servers.kind_liquid',
}

export function RackServersPanel({ model }: RackServersPanelProps) {
  const { t } = useTranslation()
  const [startU, setStartU] = useState(1)
  const [span, setSpan] = useState(
    Math.max(1, Math.round(Number((model as unknown as { defaultSpanUnits?: number }).defaultSpanUnits) || 1)),
  )
  const [kind, setKind] = useState<EquipmentKind>('fanServer')
  const [error, setError] = useState<string | null>(null)
  // The two OPTIONAL absolute depth boxes (spec §7.1). null — an empty box —
  // means the rack plane, `defaultServerDepthOffsets`, i.e. exactly what
  // creating a server did before the stacked-servers spec.
  const [inletOffset, setInletOffset] = useState<number | null>(null)
  const [outletOffset, setOutletOffset] = useState<number | null>(null)

  if (model.kind !== 'rack') return null
  const slotCount = rackSlotCount(model)
  const rows = rackServerRows(model)

  function add() {
    const depth =
      inletOffset === null && outletOffset === null
        ? undefined
        : {
            ...(inletOffset === null ? {} : { inletOffset }),
            ...(outletOffset === null ? {} : { outletOffset }),
          }
    const result = addRackServerQuick(model.uid, startU - 1, span, kind, depth)
    if (result.ok) {
      setError(null)
      return
    }
    setError(
      result.error === 'occupied'
        ? t('error.slot_occupied')
        : result.error === 'out-of-range'
          ? t('error.slot_in_safety_zone')
          : result.error === 'blocked'
            ? t('error.span_blocked')
            : result.error === 'depth-range'
              ? t('error.server_depth_range')
              : t('rack.servers.add_failed'),
    )
  }

  return (
    <div className="prop-group" style={{ borderLeftColor: '#7d5bed' }}>
      <div className="prop-title" style={{ color: '#9b7dff' }}>
        {t('rack.servers.title')}{' '}
        <span style={{ opacity: 0.6, fontWeight: 'normal' }}>({slotCount}U)</span>
      </div>
      <div
        className="prop-row"
        style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-start' }}
      >
        <label style={{ flex: '0 0 auto', minWidth: 'auto' }} htmlFor="rsp-startu">
          {t('rack.servers.start_u')}
        </label>
        <NumberField
          id="rsp-startu"
          value={startU}
          step={1}
          min={1}
          max={Math.max(1, slotCount)}
          decimals={0}
          width={52}
          onCommit={(v) => setStartU(Math.round(v))}
        />
        <label style={{ flex: '0 0 auto', minWidth: 'auto' }} htmlFor="rsp-span">
          {t('rack.servers.span')}
        </label>
        <NumberField
          id="rsp-span"
          value={span}
          step={1}
          min={1}
          max={Math.max(1, slotCount)}
          decimals={0}
          width={52}
          onCommit={(v) => setSpan(Math.round(v))}
        />
        <select
          id="rsp-kind"
          value={kind}
          style={{ flex: '0 0 auto', width: 'auto', minWidth: 'auto' }}
          onChange={(e) => setKind(e.target.value as EquipmentKind)}
        >
          <option value="fanServer">{t('rack.servers.kind_fan')}</option>
          <option value="nonFan">{t('rack.servers.kind_nonfan')}</option>
          <option value="liquidServer">{t('rack.servers.kind_liquid')}</option>
        </select>
      </div>
      {/* Optional absolute placement: setback from the inlet face and from the
          outlet face, in metres. Empty is the rack plane — today's behaviour —
          and a value that breaks §3.1 or intersects a server already at these
          U's is refused with a message instead of silently clamped or dropped. */}
      <div
        className="prop-row"
        style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'flex-start' }}
      >
        <label style={{ flex: '0 0 auto', minWidth: 'auto', fontSize: '0.7rem', color: '#4af' }} htmlFor="rsp-inlet-offset">
          {t('equipment.extra_inlet')}
        </label>
        <NumberField
          id="rsp-inlet-offset"
          value={inletOffset}
          step={0.01}
          min={0}
          decimals={3}
          width={64}
          onCommit={(v) => setInletOffset(v)}
          onClear={() => setInletOffset(null)}
        />
        <label style={{ flex: '0 0 auto', minWidth: 'auto', fontSize: '0.7rem', color: '#f64' }} htmlFor="rsp-outlet-offset">
          {t('equipment.extra_outlet')}
        </label>
        <NumberField
          id="rsp-outlet-offset"
          value={outletOffset}
          step={0.01}
          min={0}
          decimals={3}
          width={64}
          onCommit={(v) => setOutletOffset(v)}
          onClear={() => setOutletOffset(null)}
        />
      </div>
      <button className="btn primary" id="rsp-add" style={{ width: '100%', margin: 0 }} onClick={add}>
        + {t('rack.servers.add')}
      </button>
      <div id="rsp-err" className="error" style={{ minHeight: '1em', margin: '2px 0' }}>
        {error ?? ''}
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: '0.8rem', opacity: 0.6, padding: '4px 0' }}>
          {t('rack.servers.empty')}
        </div>
      ) : (
        rows.map((row) => (
          <div
            className="prop-row"
            key={row.id}
            id={`rsp-row-${row.id}`}
            style={{ justifyContent: 'space-between', alignItems: 'center', gap: 6 }}
          >
            <span
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                flex: '1 1 auto',
                minWidth: 0,
              }}
              title={row.name ?? row.id}
            >
              {row.name ?? row.id}
            </span>
            <span style={{ flex: '0 0 auto', opacity: 0.8, fontFamily: 'monospace' }}>
              U{row.startUnit + 1}–U{row.endUnit + 1}
            </span>
            <span className="badge" style={{ flex: '0 0 auto' }}>
              {t(KIND_LABEL[(row.kind as EquipmentKind) in KIND_LABEL ? (row.kind as EquipmentKind) : 'fanServer'])}
            </span>
            <button
              className="btn"
              id={`rsp-del-${row.id}`}
              title={t('rack.servers.delete_title')}
              style={{ flex: '0 0 auto', width: 'auto', padding: '0 8px', lineHeight: 1.6 }}
              onClick={() => removeEquipment(model.uid, row.id)}
            >
              ✕
            </button>
          </div>
        ))
      )}
    </div>
  )
}
