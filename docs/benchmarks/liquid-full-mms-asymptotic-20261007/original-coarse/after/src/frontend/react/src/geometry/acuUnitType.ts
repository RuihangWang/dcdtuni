/**
 * ACU unit type: a floor-standing CRAH (the box every ACU used to be) or a
 * wall-mounted split unit ("挂机"), drawn by the shared geometry builder's
 * `renderWallAcuModel`: left, right and top faces plus one continuous front
 * that bends through a quarter circle of `geometry.frontRadius` into the
 * bottom. Switching type does not touch the patches: a supply patch that was
 * on the planar front keeps its (x, y, w, h), now read in the front's unrolled
 * coordinates.
 */

import * as store from '../projectStore'
import { syncAcuSupplyReturnFaces } from './patchPhysics'

type Bag = Record<string, unknown>

export function setAcuUnitType(uid: string, unitType: string) {
  store.edit('setAcuUnitType', `${uid} ${unitType}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
    if (!model) return
    const cooling = (model.cooling ?? (model.cooling = {})) as Bag
    if (unitType === 'wallMounted') cooling.unitType = 'wallMounted'
    else delete cooling.unitType
    syncAcuSupplyReturnFaces(model as { kind?: string; geometry?: unknown })
  })
}

export function setAcuFrontRadius(uid: string, radius: number) {
  store.edit('setAcuFrontRadius', `${uid} ${radius}`, (draft) => {
    const model = store.findConstruction(draft, uid)?.model as unknown as Bag | undefined
    if (!model) return
    const geometry = (model.geometry ?? (model.geometry = {})) as Bag
    if (Number.isFinite(radius) && radius > 0) geometry.frontRadius = radius
    else delete geometry.frontRadius
  })
}

export function isWallMountedAcu(model: unknown): boolean {
  const m = model as { kind?: unknown; cooling?: Bag } | null | undefined
  return m?.kind === 'acu' && m.cooling?.unitType === 'wallMounted'
}
