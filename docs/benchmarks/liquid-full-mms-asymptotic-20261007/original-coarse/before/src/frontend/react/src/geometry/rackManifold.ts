/**
 * A liquid-cooled rack's manifold: layout, mounting face, pipe bore and offset.
 *
 * Ports the `_lmHtml` block (STL_generate.html:9057-9086) and its three writers
 * `setManifoldLayout` (:9393), `setManifoldMountFace` (:9404) and
 * `setManifoldParam` (:9415).
 *
 * Two details that are easy to get wrong:
 *
 * - **The panel reads metres and shows millimetres.** `supplyPipe.diameter` and
 *   `offsetFromInlet` are stored in metres; the inputs are mm, so every read is
 *   x1000 and every write is /1000. Storing what the box shows would make a
 *   25 mm pipe 25 m wide.
 * - **Diameter and offset are written to BOTH pipes.** The panel offers one box
 *   for the pair because supply and return are the same run; writing only the
 *   supply side leaves a return pipe at the default bore.
 *
 * The defaults are NOT re-declared here — `syncRackModelState` normalises
 * `cooling.liquidManifold` through `getDefaultRackState()` on every rack write
 * (rack_state.js:308-320), so a second copy of the numbers would be a second
 * opinion the moment one of them changed. `manifoldView` falls back to the same
 * values only for the read, and the writers let the sync materialise the block.
 */

import type { Construction } from '../schema/cfdproj'
import { rackState } from '../legacy/cfdprojModules'
import * as store from '../projectStore'

export type ManifoldLayout = 'both-left' | 'both-right' | 'split'
export type ManifoldMountFace = 'inlet' | 'outlet'

type Bag = Record<string, unknown>

interface RackDefaults {
  liquidManifold: {
    layout: string
    mountFace: string
    supplyPipe: { diameter: number; offsetFromInlet: number }
    returnPipe: { diameter: number; offsetFromInlet: number }
    pipeSpacing: number
  }
}

function defaultManifold(): RackDefaults['liquidManifold'] {
  const rs = rackState() as unknown as { getDefaultRackState(): RackDefaults }
  return rs.getDefaultRackState().liquidManifold
}

/** True when the rack is liquid-cooled, i.e. when the manifold section shows. */
export function isLiquidCooled(rack: Construction | null): boolean {
  const cooling = ((rack as (Construction & { cooling?: Bag }) | null)?.cooling ?? {}) as Bag
  return cooling.type === 'liquid'
}

export interface ManifoldView {
  layout: string
  mountFace: ManifoldMountFace
  /** Millimetres, as the two number boxes show them. */
  diameterMm: number
  offsetMm: number
}

/**
 * What the panel displays for a rack.
 *
 * A rack with no manifold block reads as the DEFAULT block, because that is
 * what the legacy shows: `_lmHtml` materialises the defaults before rendering
 * (STL_generate.html:9055), so its layout box says `split`, never `both-left`.
 * The `|| 'both-left'` below is the legacy's own fallback and only bites for a
 * block that exists but carries no layout.
 */
export function manifoldView(rack: Construction | null): ManifoldView {
  const cooling = ((rack as (Construction & { cooling?: Bag }) | null)?.cooling ?? {}) as Bag
  const stored = cooling.liquidManifold
  const lm = (stored && typeof stored === 'object' ? stored : defaultManifold()) as Bag
  const supply = (lm.supplyPipe ?? {}) as Bag
  const diameter = Number(supply.diameter) || 0.025
  const offset = Number(supply.offsetFromInlet) || 0.1
  return {
    layout: (lm.layout as string) || 'both-left',
    mountFace: lm.mountFace === 'outlet' ? 'outlet' : 'inlet',
    diameterMm: diameter * 1000,
    offsetMm: offset * 1000,
  }
}

/** Apply a mutation to one rack's manifold block, creating it if it is absent. */
function editManifold(uid: string, action: string, detail: string, mutate: (lm: Bag) => void) {
  store.edit(action, detail, (draft) => {
    const rack = store.findConstruction(draft, uid)?.model as
      | (Construction & { cooling?: Bag })
      | undefined
    if (!rack || rack.kind !== 'rack') return
    const cooling = (rack.cooling ?? (rack.cooling = {})) as Bag
    if (!cooling.liquidManifold || typeof cooling.liquidManifold !== 'object') {
      cooling.liquidManifold = defaultManifold()
    }
    mutate(cooling.liquidManifold as Bag)
    // Same rule as every other rack write in this app: the normaliser clamps
    // the block and re-derives the slot table, so a field written on its own
    // would leave the rack disagreeing with its own geometry.
    rackState().syncRackModelState(rack)
  })
}

export function setManifoldLayout(uid: string, layout: string) {
  editManifold(uid, 'setManifoldLayout', `Set manifold layout to ${layout}`, (lm) => {
    lm.layout = layout
  })
}

export function setManifoldMountFace(uid: string, face: string) {
  const value: ManifoldMountFace = face === 'outlet' ? 'outlet' : 'inlet'
  editManifold(uid, 'setManifoldMountFace', `Set manifold mounting face to ${face}`, (lm) => {
    lm.mountFace = value
  })
}

/** `mm` as the box gives it; stored in metres on BOTH pipes. */
export function setManifoldParam(uid: string, param: 'diameter' | 'offset', mm: number) {
  if (!Number.isFinite(mm)) return
  const metres = mm / 1000
  editManifold(uid, 'setManifoldParam', `Set manifold ${param} to ${metres}`, (lm) => {
    const supply = (lm.supplyPipe ?? (lm.supplyPipe = {})) as Bag
    const ret = (lm.returnPipe ?? (lm.returnPipe = {})) as Bag
    if (param === 'diameter') {
      supply.diameter = metres
      ret.diameter = metres
    } else {
      supply.offsetFromInlet = metres
      ret.offsetFromInlet = metres
    }
  })
}
