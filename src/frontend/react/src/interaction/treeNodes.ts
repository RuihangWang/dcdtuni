/**
 * The feature tree's sub-hierarchy: what hangs UNDER a model.
 *
 * A port of the per-kind branches in `renderModelNode` (STL_generate.html:8020
 * onward), kept as a pure function of the project so the shape can be tested
 * without a DOM and compared against the legacy tree node for node.
 *
 * The four shapes, which are not interchangeable:
 *
 *   rack        🧰 Equipments → server → its six faces → patches
 *               🪟 Partitions → one row per slot divider
 *   pipe        📍 control points, its two end ports, its blind ends, and one
 *               row per valve or T-junction fitting — a pipe has no faces to
 *               offer, and six of them would invite a boundary condition on a
 *               waypoint list
 *   wall/baffle/floor
 *               the PRIMARY face's patches only (top for a floor, front for the
 *               rest) plus its wall regions — a wall is one sheet, and offering
 *               six faces would invite a boundary condition on its edge
 *   custom      🧩 region → patches
 *   everything else
 *               📄 six faces → patches
 *
 * Selection targets travel with each node. A face and a patch are different
 * things to select — the inspector shows a different panel for each — so the
 * node carries what it selects rather than the tree inferring it from depth.
 */

import type { Construction } from '../schema/cfdproj'
import { customRegionGroups } from '../geometry/customRegions'
import { findDoor, setDoorEnabled, setDoorRender } from '../geometry/rackDetail'
import { isSlotDeleted } from '../geometry/rackSlots'
import { equipmentEndUnit, equipmentList } from '../geometry/rackSelection'
import { rackFaceLabel } from '../geometry/rackFaceLabels'
import { t } from '../i18n'

export interface TreeSelection {
  type:
    | 'model'
    | 'face'
    | 'patch'
    | 'equipment'
    | 'serverFace'
    | 'serverPatch'
    | 'partition'
    | 'rackSlot'
    | 'region'
    | 'wallRegion'
    | 'fitting'
  uid: string
  equipmentId?: string
  faceId?: string
  patchId?: string
  index?: number
  regionName?: string
  /** A valve or T-junction on a pipe, by its `cooling.valves[].id`. */
  fittingId?: string
  /** Which face of a rack a slot selection came from. */
  side?: 'front' | 'back'
  /**
   * Every selected slot, when more than one.
   *
   * Real equipment is 2U, 4U, 10U — a rack is populated by selecting a RANGE
   * and merging it, so a slot selection has to be able to be plural.
   */
  indices?: number[]
}

export interface TreeChild {
  /** Stable across renders; the collapse state is keyed on it. */
  key: string
  label: string
  badge?: string
  /** A colour swatch, for patches and equipment. */
  dot?: string
  select?: TreeSelection
  children?: TreeChild[]
  /**
   * A visibility checkbox on the row, as the door row has
   * (STL_generate.html:7998).
   *
   * A callback rather than a flag the tree interprets: only one row in the tree
   * has one, and giving the component a table of row types to switch on is how
   * the next such row ends up rendered by a copy of it.
   */
  checkbox?: { checked: boolean; title?: string; onChange: (next: boolean) => void }
  /** A ✖ (or other) button at the end of the row. */
  action?: { icon: string; title?: string; onClick: () => void }
  /**
   * Start folded, until the user opens it.
   *
   * Only the rack face rows use this, and for a measured reason: each opens
   * into one row per U, so on `case/Base604` (142 racks) the tree went from
   * 16,186 rows to 28,682 and a click on any row took 1.58 s to paint. The
   * legacy has no such default — its tree is the same shape — but it is the
   * right one here, and the rows are two clicks away rather than gone.
   */
  defaultCollapsed?: boolean
}

interface FaceLike {
  displayName?: string
  label?: string
  patches?: { id: string; name?: string; color?: string; type?: string; w?: number; h?: number; r?: number }[]
}

/** `face.displayName || face.label`, the legacy `getFaceDisplayName`. */
export function faceDisplayName(face: FaceLike | undefined, fallback: string): string {
  return face?.displayName || face?.label || fallback
}

/** "25×100%" for a rect, "r=30%" for a circle — the legacy patch badge. */
export function patchDetail(patch: {
  type?: string
  w?: number
  h?: number
  r?: number
}): string {
  if (patch.type === 'rect' || patch.w !== undefined) {
    return `${Math.round((patch.w ?? 0) * 100)}×${Math.round((patch.h ?? 0) * 100)}%`
  }
  return `r=${Math.round((patch.r ?? 0) * 100)}%`
}

const EQUIPMENT_FACES = ['inlet', 'outlet', 'top', 'bottom', 'left', 'right'] as const
/**
 * A server face row. Through the shared rack-face labels, so a server's inlet
 * and the rack side it faces read the same word in either language.
 */
const equipmentFaceLabel = (faceId: string) => `◻ ${rackFaceLabel(faceId)}`

const BOX_FACE_ORDER = ['front', 'back', 'left', 'right', 'top', 'bottom']

type AnyRecord = Record<string, unknown>

function facesOf(model: Construction): Record<string, FaceLike> | null {
  const faces = (model.geometry as AnyRecord | undefined)?.faces
  // V4 racks carry an ARRAY here (usually empty) while every other kind carries
  // an object keyed by face name. Treating the array as a map yields numeric
  // keys and six nodes labelled "0".."5".
  if (!faces || Array.isArray(faces)) return null
  return faces as Record<string, FaceLike>
}

function patchNodes(
  patches: FaceLike['patches'],
  keyPrefix: string,
  select: (patchId: string) => TreeSelection,
  numbered: boolean,
): TreeChild[] {
  return (patches ?? []).map((patch, i) => ({
    key: `${keyPrefix}:${patch.id}`,
    label: numbered ? `${patch.type === 'circle' ? '●' : '■'} Patch ${i + 1}` : (patch.name ?? patch.id),
    badge: numbered ? patchDetail(patch) : undefined,
    dot: patch.color ?? '#00cc66',
    select: select(patch.id),
  }))
}

/**
 * The tree key of a server row.
 *
 * Exported so a panel can jump to the server it names: the tree row carries
 * this key as `data-node`, and clicking that row IS the app's selection path,
 * so the jump is not a simulation of a click but the same code a user click
 * runs. Built here so the key and the row can never drift apart.
 */
export function serverNodeKey(uid: string, equipmentId: string): string {
  return `srv-eq:${uid}:${equipmentId}`
}

/**
 * One face of a rack, opened into its U-slots.
 *
 * The legacy's own route into a slot (STL_generate.html:7940-8002): a face row
 * badged with the slot count, one row per U from the TOP of the rack down, a
 * multi-U server collapsed into a single row whose children are its own U, and
 * the door row with its visibility checkbox and remove button.
 *
 * It matters beyond browsing. A click on an occupied slot in the viewport
 * resolves to the server's face, never to a slot (`rackFaceGesture`), so
 * without these rows nothing in the app selects ONE U of a server — and the
 * single-slot inspector that trims a server by a U is unreachable.
 */
function rackFaceChild(model: Construction, side: 'front' | 'back'): TreeChild {
  const uid = model.uid
  const geometry = (model.geometry ?? {}) as { slot?: number }
  const slotCount = Math.max(0, Number(geometry.slot ?? 42))
  const doorSide = side === 'front' ? 'inlet' : 'outlet'
  const children: TreeChild[] = []

  const slotRow = (index: number, label: string): TreeChild => ({
    key: `rack-slot:${uid}:${side}:${index}`,
    label: `▫ ${label}`,
    select: { type: 'rackSlot', uid, side, index },
  })

  const servers = equipmentList(model)
  const occupant = (i: number) =>
    servers.find((e) => i >= e.startUnit && i <= equipmentEndUnit(e)) ?? null

  // Top of the rack first, as the legacy walks `slot - 1` down to 0.
  for (let index = slotCount - 1; index >= 0; index--) {
    const equipment = occupant(index)
    if (equipment && Math.max(1, equipment.spanUnits) > 1) {
      const start = equipment.startUnit
      const end = equipmentEndUnit(equipment)
      const kids: TreeChild[] = []
      for (let u = end; u >= start; u--) kids.push(slotRow(u, slotDisplayName(u)))
      children.push({
        key: `rack-eqgroup:${uid}:${side}:${equipment.id}`,
        label: `${equipment.name ?? equipment.id}`,
        badge: `${end - start + 1}U`,
        select: { type: 'equipment', uid, equipmentId: equipment.id, side },
        children: kids,
      })
      // Skip the rest of this server: the loop's own decrement steps past its
      // first U.
      index = start
      continue
    }
    const status = equipment?.name ?? (isSlotDeleted(model, side, index) ? 'void' : 'wall')
    children.push(slotRow(index, `${slotDisplayName(index)} · ${status}`))
  }

  const door = findDoor(model, doorSide)
  if (door) {
    const open = Math.round(
      Number((door.perforation as { openAreaPct?: number } | undefined)?.openAreaPct ?? 64),
    )
    children.push({
      key: `rack-door:${uid}:${side}`,
      label: `${t(doorSide === 'inlet' ? 'rack.inlet_door' : 'rack.outlet_door')} (${open}%)`,
      checkbox: {
        checked: door.renderDoor !== false,
        title: t('tooltip.toggle_visibility'),
        onChange: (next) => setDoorRender(uid, doorSide, next),
      },
      action: {
        icon: '',
        title: t('tooltip.delete'),
        onClick: () => setDoorEnabled(uid, doorSide, false),
      },
    })
  }

  // Named by the air it passes — the front is the inlet, the back the outlet
  // (`rackFaceLabel`). V4 racks hold no per-side record that could carry a
  // name (`geometry.faces` is the door entries), so this read the raw key and
  // the row said "📄 front" / "📄 back". The key stays the selection's data.
  return {
    key: `face:${uid}:${side}`,
    label: `${rackFaceLabel(side)}`,
    badge: String(slotCount),
    select: { type: 'face', uid, faceId: side },
    defaultCollapsed: true,
    children,
  }
}

/** `U12` — the legacy's `getRackSlotDisplayName`, 1-based. */
function slotDisplayName(index: number): string {
  return `U${index + 1}`
}

/** The rack's servers and slot partitions. */
function rackChildren(model: Construction): TreeChild[] {
  const out: TreeChild[] = []
  const constructions = (model as unknown as AnyRecord).constructions as AnyRecord | undefined
  const servers = (constructions?.servers ?? {}) as Record<string, AnyRecord>
  const serverList = Object.values(servers)

  out.push({
    key: `rack-equipment:${model.uid}`,
    label: 'Equipments',
    badge: String(serverList.length),
    children: serverList.map((equipment) => {
      const id = String(equipment.uid ?? '')
      const meta = equipment.meta as AnyRecord | undefined
      const faces = (equipment.faces ?? {}) as Record<string, FaceLike>
      return {
        key: serverNodeKey(model.uid, id),
        label: String(meta?.name ?? id),
        dot: (faces.inlet as { color?: string } | undefined)?.color ?? '#d14b4b',
        select: { type: 'equipment', uid: model.uid, equipmentId: id },
        children: EQUIPMENT_FACES.map((fid) => {
          const face = faces[fid] ?? { patches: [] }
          const patches = face.patches ?? []
          return {
            key: `srv-face:${model.uid}:${id}:${fid}`,
            label: equipmentFaceLabel(fid),
            // "1 patch" only when there are any, matching the legacy badge.
            badge: patches.length ? `${patches.length} patch` : undefined,
            select: { type: 'serverFace', uid: model.uid, equipmentId: id, faceId: fid },
            children: patchNodes(
              patches,
              `srv-patch:${model.uid}:${id}:${fid}`,
              (patchId) => ({
                type: 'serverPatch',
                uid: model.uid,
                equipmentId: id,
                faceId: fid,
                patchId,
              }),
              true,
            ),
          }
        }),
      }
    }),
  })

  const partitions = (constructions?.partitions ?? []) as {
    index: number
    name: string
    deleted?: boolean
  }[]
  out.push({
    key: `rack-partition:${model.uid}`,
    label: 'Partitions',
    badge: String(partitions.length),
    children: partitions.map((partition) => ({
      key: `rack-partition:${model.uid}:${partition.index}`,
      // A deleted partition is still LISTED. It is a hole the airflow goes
      // through, and hiding the row would leave no way to put it back.
      label: `▫ ${partition.name}${partition.deleted ? ' (deleted)' : ''}`,
      select: { type: 'partition', uid: model.uid, index: partition.index },
    })),
  })

  // Last, not first: each face opens into 42 rows, and putting them above the
  // two groups would push Equipments and Partitions off the panel on any rack
  // worth looking at.
  for (const side of ['front', 'back'] as const) {
    out.push(rackFaceChild(model, side))
  }

  return out
}

/** Wall, baffle and floor: the patches of the one face that means anything. */
function sheetChildren(model: Construction, wallRegions: readonly string[]): TreeChild[] {
  const faces = facesOf(model)
  const primary = model.kind === 'floor' ? 'top' : 'front'
  const face = faces?.[primary]
  const patches = face?.patches ?? []
  const out = patchNodes(
    patches,
    `patch:${model.uid}:${primary}`,
    (patchId) => ({ type: 'patch', uid: model.uid, faceId: primary, patchId }),
    false,
  )
  // Regions only exist once patches have split the sheet. Without them the
  // whole face is one piece and a "region 1" row would be a lie.
  if (patches.length > 0) {
    wallRegions.forEach((name, index) => {
      out.push({
        key: `wallRegion:${model.uid}:${primary}:${index}`,
        label: `▫ ${name}`,
        select: { type: 'wallRegion', uid: model.uid, faceId: primary, index },
      })
    })
  }
  return out
}

/**
 * An imported STL: one row per solid region, its patches underneath.
 *
 * Not six faces. The solids in the file ARE the boundaries OpenFOAM will get,
 * so a patch belongs to the region it was projected onto, and the six
 * projection faces are an implementation detail of where the beam points. The
 * legacy tree draws exactly this shape (STL_generate.html:7907), and without it
 * a custom model showed six face rows a patch could never appear under.
 */
function customChildren(model: Construction): TreeChild[] {
  const out: TreeChild[] = []
  for (const [key, entries] of customRegionGroups(model)) {
    const regionName = key === '__whole__' ? null : key
    out.push({
      key: `region:${model.uid}:${key}`,
      label: `${regionName ?? t('label.whole_model')}`,
      badge: String(entries.length),
      select: { type: 'region', uid: model.uid, ...(regionName ? { regionName } : {}) },
      children: entries.map(({ faceId, patch }) => ({
        key: `patch:${model.uid}:${faceId}:${patch.id}`,
        label: patch.name ?? patch.id,
        dot: patch.color ?? '#00cc66',
        select: {
          type: 'patch' as const,
          uid: model.uid,
          faceId,
          patchId: patch.id,
          ...(regionName ? { regionName } : {}),
        },
      })),
    })
  }
  return out
}

export interface PipeValve {
  id: string
  type?: string
  connectedRackId?: string
  connectedManifoldPortId?: string
}

/** Every valve and T-junction fitting on a pipe, in the order it carries them. */
export function pipeValves(model: Construction): PipeValve[] {
  const cooling = (model as unknown as AnyRecord).cooling as AnyRecord | undefined
  const valves = cooling?.valves
  if (!Array.isArray(valves)) return []
  return valves.filter((v): v is PipeValve => !!v && typeof (v as PipeValve).id === 'string')
}

/**
 * A pipe segment's tree children — the port of the `pipe_segment` branch of
 * `renderModelNode` (STL_generate.html:8135).
 *
 * A `pipe_junction` reaches the same dispatch and gets NOTHING, exactly as the
 * legacy does: the junction's three ports belong to the pipes attached to it,
 * and listing them under the fitting would show the same connection twice.
 *
 * The rows are read-only except the fittings, which select. A valve is
 * otherwise reachable only by hitting it in the 3D view, and a 60 mm cube on a
 * 25 mm pipe is a small target.
 */
function pipeChildren(model: Construction, opts: ChildOptions): TreeChild[] {
  if (model.kind !== 'pipe_segment') return []
  const geometry = (model.geometry ?? {}) as AnyRecord
  const cooling = ((model as unknown as AnyRecord).cooling ?? {}) as AnyRecord
  const connection = (cooling.connection ?? {}) as AnyRecord
  const waypoints = Array.isArray(geometry.waypoints) ? geometry.waypoints : null
  // No waypoint list at all is not an empty pipe, it is a model that has never
  // been through the pipe editor — the legacy skips the whole block for it.
  if (!waypoints) return []

  const out: TreeChild[] = [
    {
      key: `pipe-cp:${model.uid}`,
      label: `${t('pipe.control_points')}`,
      badge: String(waypoints.length),
    },
  ]

  const start = connection.startPortId
  if (typeof start === 'string' && start) {
    out.push({
      key: `pipe-start:${model.uid}`,
      label: `${t('pipe.start_port')}: ${start}`,
      dot: '#2196F3',
    })
  }
  const end = connection.endPortId
  if (typeof end === 'string' && end) {
    out.push({
      key: `pipe-end:${model.uid}`,
      label: `${t('pipe.end_port')}: ${end}`,
      dot: '#F44336',
    })
  }

  const blindEnds = (connection.blindEnds ?? {}) as AnyRecord
  if (blindEnds.start) {
    out.push({ key: `pipe-blind-start:${model.uid}`, label: t('liquid.blind_start') })
  }
  if (blindEnds.end) {
    out.push({ key: `pipe-blind-end:${model.uid}`, label: t('liquid.blind_end') })
  }

  for (const valve of pipeValves(model)) {
    const isTee = valve.type === 'tee_junction'
    const suffix = valve.id.split('_').pop() ?? valve.id
    // The legacy resolves the rack by `rm.id === v.connectedRackId`, and V4
    // models carry `uid` rather than `id`, so the arrow suffix never renders
    // there. Supplied by the caller here rather than derived, because this
    // module is handed one model and the rack is a different one.
    const rackName = valve.connectedRackId ? opts.modelName?.(valve.connectedRackId) : null
    out.push({
      key: `pipe-fitting:${model.uid}:${valve.id}`,
      label: `${isTee ? 'T-Junction' : 'Valve'}_${suffix}${
        rackName ? ` → ${rackName}` : ''
      }`,
      badge: isTee ? 'TEE' : 'FCV',
      dot: isTee ? '#26C6DA' : '#FF9800',
      select: { type: 'fitting', uid: model.uid, fittingId: valve.id },
    })
  }

  return out
}

/** Boxes, ACUs, CDUs: six faces, each with its patches. */
function boxChildren(model: Construction): TreeChild[] {
  const faces = facesOf(model)
  if (!faces) return []
  const order = BOX_FACE_ORDER.filter((f) => faces[f]).concat(
    Object.keys(faces).filter((f) => !BOX_FACE_ORDER.includes(f)),
  )
  return order.map((fid) => {
    const face = faces[fid] as FaceLike
    const patches = face.patches ?? []
    return {
      key: `face:${model.uid}:${fid}`,
      label: `${faceDisplayName(face, fid)}`,
      badge: String(patches.length),
      select: { type: 'face', uid: model.uid, faceId: fid },
      children: patchNodes(
        patches,
        `patch:${model.uid}:${fid}`,
        (patchId) => ({ type: 'patch', uid: model.uid, faceId: fid, patchId }),
        false,
      ),
    }
  })
}

export interface ChildOptions {
  /**
   * Export names of the wall regions of this model's primary face, in wall
   * order. They come from the BUILT scene — the legacy reads them off the
   * rendered meshes — so the tree cannot derive them from the project alone.
   */
  wallRegions?: readonly string[]
  /**
   * Name for another model, by uid — for the rack a pipe fitting is wired to.
   *
   * Optional because this module is given ONE model: without it the fitting row
   * renders with no arrow suffix, which is what the legacy tree shows today
   * (its lookup is by the pre-V4 `.id` and never matches).
   */
  modelName?: (uid: string) => string | null
}

/** An air pipe's rows: its waypoint count and the pipes it is joined to. */
function airPipeChildren(model: Construction, opts: ChildOptions): TreeChild[] {
  const geometry = (model.geometry ?? {}) as AnyRecord
  const waypoints = Array.isArray(geometry.waypoints) ? geometry.waypoints : []
  const joins = Array.isArray(geometry.joins) ? (geometry.joins as AnyRecord[]) : []
  const out: TreeChild[] = [
    { key: `pipe-cp:${model.uid}`, label: `${t('pipe.control_points')}`, badge: String(waypoints.length) },
  ]
  for (const j of joins) {
    const uid = String(j.pipeUid ?? '')
    if (!uid) continue
    out.push({
      key: `pipe-join:${model.uid}:${j.end}`,
      label: `${t('pipe.joined_with', { name: opts.modelName?.(uid) ?? uid })}`,
      select: { type: 'model', uid },
    })
  }
  return out
}

export function modelChildren(model: Construction, opts: ChildOptions = {}): TreeChild[] {
  const kind = model.kind
  let children: TreeChild[]
  if (kind === 'rack') children = rackChildren(model)
  else if (kind === 'custom') children = customChildren(model)
  else if (kind === 'pipe_segment' || kind === 'pipe_junction') {
    children = pipeChildren(model, opts)
  } else if (kind === 'pipe') {
    children = airPipeChildren(model, opts)
  } else if (kind === 'wall' || kind === 'baffle' || kind === 'floor') {
    children = sheetChildren(model, opts.wallRegions ?? [])
  } else children = boxChildren(model)

  // The solids an imported STL brought with it, listed for every kind but
  // `custom` (where the regions ARE the model's own tree).
  const solidRegions = ((model as unknown as AnyRecord).solidRegions ?? []) as string[]
  if (kind !== 'custom' && solidRegions.length > 0) {
    children.push({
      key: `regions:${model.uid}`,
      label: '原始区域',
      badge: String(solidRegions.length),
      children: solidRegions.map((name) => ({
        key: `region:${model.uid}:${name}`,
        label: `▫ ${name}`,
        select: { type: 'region', uid: model.uid, regionName: name },
      })),
    })
  }
  return children
}
