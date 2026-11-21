import type { Construction } from '../schema/cfdproj'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { RemainingAreaBc } from './PatchInspector'

/**
 * The CFD physics of a sheet — a floor's room-boundary role, and the boundary
 * condition of the REMAINING area of a floor or a baffle.
 *
 * ## Why the remaining area belongs HERE
 *
 * On a raised floor the remaining area is most of the floor, and on a
 * containment panel most of the panel: it is the common case, not the leftover.
 * A whole plate made perforated is one control; described patch by patch it is
 * one tile at a time, and the gaps BETWEEN the tiles stay solid wall with
 * nothing on screen saying so.
 *
 * React had the control (`RemainingAreaBc`) and no route to it for the case
 * that matters. It was mounted in `FaceInspector` and in `WallRegionInspector`,
 * and a sheet reaches NEITHER while it has no patches: `sheetChildren`
 * (interaction/treeNodes.ts:220) hangs patches straight off the model and emits
 * no face row, `resolveViewportSelection` returns null for a planar body
 * (interaction/picking.ts:137), and the wall-region rows exist only once
 * patches have split the sheet. So a fresh floor or baffle could not be made
 * perforated at all.
 *
 * The legacy puts it on the MODEL inspector, unconditionally, for exactly this
 * reason — `renderBaffleCfdInspector` (STL_generate.html:8848) and
 * `renderFloorCfdInspector` (:8887), both embedded at :8447-8448.
 *
 * ## Whether this floor IS the room's boundary.
 *
 * A raised floor inside the room and the slab that closes the domain are the
 * same shape and completely different boundaries: one is an internal
 * `wallBaffle` with air on both sides, the other is the solid `wall` the
 * domain ends at. Getting it wrong either leaks the plenum into the room or
 * seals the room off from its own supply.
 *
 * Setting it re-couples the remaining-area default, exactly as
 * `updateFloorRoomBoundaryType` does (STL_generate.html:28899): a room boundary
 * becomes `wall`, anything else `wallBaffle`. That coupling is the point — the
 * two settings are one decision, and leaving the default behind is how a
 * raised floor ends up solid.
 */

export interface SheetCfdSectionProps {
  model: Construction
}

export function SheetCfdSection({ model }: SheetCfdSectionProps) {
  const { t } = useTranslation()
  const isFloor = model.kind === 'floor'
  if (!isFloor && model.kind !== 'baffle') return null

  const cooling = ((model as unknown as { cooling?: Record<string, unknown> }).cooling ?? {}) as
    Record<string, unknown>
  const raw = String(cooling.roomBoundaryLocation ?? 'none')
  const location = raw === 'top' || raw === 'bottom' ? raw : 'none'

  function set(value: string) {
    const normalised = value === 'top' || value === 'bottom' ? value : 'none'
    store.edit('updateFloorRoomBoundaryType', `${model.uid} ${normalised}`, (draft) => {
      const target = store.findConstruction(draft, model.uid)?.model as unknown as {
        cooling?: Record<string, unknown>
        isRoomBoundary?: boolean
      }
      if (!target) return
      const bag = (target.cooling ?? (target.cooling = {})) as Record<string, unknown>
      bag.roomBoundaryLocation = normalised
      target.isRoomBoundary = normalised !== 'none'
      // One decision, two fields: a boundary floor is solid, an internal one is
      // a baffle with air on both sides.
      bag.defaultBoundaryType = normalised === 'none' ? 'wallBaffle' : 'wall'
    })
  }

  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {isFloor ? t('inspector.floor_cfd_physics') : t('inspector.baffle_cfd_physics')}
      </div>
      {isFloor && (
        <>
          <div className="prop-row">
            <label htmlFor="floor-room-boundary">{t('label.room_boundary_type')}</label>
            <select id="floor-room-boundary" value={location} onChange={(e) => set(e.target.value)}>
              <option value="none">{t('option.room_boundary_none')}</option>
              <option value="top">{t('option.room_boundary_top')}</option>
              <option value="bottom">{t('option.room_boundary_bottom')}</option>
            </select>
          </div>
          <div className="prop-hint">
            {location === 'none' ? t('hint.bc_wall_baffle') : t('hint.bc_wall')}
          </div>
        </>
      )}
      {/* Boxed off, as the legacy boxes it: inside a floor's panel the room
          boundary above and the remaining area below are two different
          decisions, and run together they read as one. */}
      <div className="remaining-area-block" id="remaining-area-block">
        {/* The floor's own heading comes from `RemainingAreaBc`'s `default`
            variant; the baffle has none there, and the legacy gives it this
            one (STL_generate.html:8855). */}
        {!isFloor && <div className="remaining-area-title">{t('label.remaining_region')}</div>}
        <RemainingAreaBc model={model} variant="default" />
      </div>
    </div>
  )
}
