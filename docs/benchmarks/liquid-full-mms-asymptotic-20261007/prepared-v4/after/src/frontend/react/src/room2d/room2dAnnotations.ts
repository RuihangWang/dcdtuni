/**
 * Publication annotations for the floor plan: hot/cold aisles and column labels.
 *
 * Ported from `_drawPaperAnnotations` (`room_2d_editor.js:565`), split so the
 * DECISIONS — which gap between two rack rows is a hot aisle — are pure and
 * testable, and only the painting is left to the canvas. This is the artefact
 * the 3-agent Builder/Evaluator SOP screenshots, so a mislabelled aisle is a
 * mislabelled figure in the paper.
 *
 * How an aisle is decided, in the legacy's own order:
 *
 *   1. group racks into COLUMNS by world X (within 0.5 m);
 *   2. work out whether the racks mostly face along X or along Z, by counting
 *      racks rather than columns — a single wide row must not outvote six racks;
 *   3. re-group along the FACING direction, because the aisle runs between rows
 *      that face each other, not between whatever X happened to line up;
 *   4. a gap is HOT when the two rows blow INTO it and COLD when they draw FROM
 *      it. Anything else — two rows facing the same way — is not an aisle.
 *
 * The 6 m cut-off keeps two rows at opposite ends of a hall from being called an
 * aisle, and the 5 m baffle radius marks a contained one.
 */

import type { Quat } from '../schema/cfdproj'
import { applyQuat, type PlanModel } from './room2dModels'

export type AisleType = 'HOT' | 'COLD'

export interface Aisle {
  type: AisleType
  worldX: number
  worldZ: number
  /** Label runs along Z, so it is drawn turned a quarter turn. */
  vertical: boolean
  contained: boolean
}

export interface ColumnLabel {
  text: string
  worldX: number
  worldZ: number
}

export interface Annotations {
  aisles: Aisle[]
  columns: ColumnLabel[]
  /** The first rack, whose width and depth are called out on the figure. */
  dimension: { model: PlanModel; width: number; depth: number } | null
  /** `HOT` / `COLD_C` … — the form `room2d.getDebugState().aisleLabels` reports. */
  labels: string[]
}

const EMPTY: Annotations = { aisles: [], columns: [], dimension: null, labels: [] }

/** A rack's outlet direction: local +Z turned by its own quaternion. */
export function outletDirection(q: Quat): { x: number; z: number } {
  const v = applyQuat({ x: 0, y: 0, z: 1 }, q)
  return { x: v.x, z: v.z }
}

interface Row {
  /** Position along the FACING axis — what the aisle sits between. */
  pos: number
  /** +1 when the row's outlets point toward increasing `pos`. */
  outSign: number
  /** Extent across the aisle, used to centre the label. */
  perpMin: number
  perpMax: number
  racks: PlanModel[]
}

export function computeAnnotations(models: PlanModel[]): Annotations {
  const racks = models.filter((m) => m.kind === 'rack' && m.enabled)
  if (racks.length === 0) return EMPTY

  const columns = groupColumns(racks)
  const facesX = dominantFacingIsX(columns)
  const rows = facesX ? columnsAsRows(columns) : groupRowsByZ(racks)

  const baffles = models.filter((m) => m.kind === 'baffle' && m.enabled)
  const aisles = detectAisles(rows, facesX, baffles)

  return {
    aisles,
    columns: columnLabels(columns),
    dimension: { model: racks[0]!, width: racks[0]!.size.x, depth: racks[0]!.size.z },
    labels: aisles.map((a) => a.type + (a.contained ? '_C' : '')),
  }
}

interface Column {
  x: number
  racks: PlanModel[]
  outDirX: number
  outDirZ: number
  minZ: number
  maxZ: number
}

/** Racks whose anchors sit within 0.5 m in X are one column. */
export function groupColumns(racks: PlanModel[]): Column[] {
  const cols: { x: number; racks: PlanModel[] }[] = []
  for (const r of [...racks].sort((a, b) => a.location.x - b.location.x)) {
    const hit = cols.find((c) => Math.abs(c.x - r.location.x) < 0.5)
    if (hit) hit.racks.push(r)
    else cols.push({ x: r.location.x, racks: [r] })
  }
  return cols.map((c) => {
    const x = c.racks.reduce((s, r) => s + r.location.x, 0) / c.racks.length
    const dir = outletDirection(c.racks[0]!.quaternion)
    const zs = c.racks.map((r) => r.location.z)
    return {
      x,
      racks: c.racks,
      outDirX: dir.x,
      outDirZ: dir.z,
      minZ: Math.min(...zs),
      maxZ: Math.max(...zs) + (c.racks[0]!.size.z || 1.2),
    }
  })
}

/** Counted by RACKS, not by columns: one wide row must not outvote six racks. */
function dominantFacingIsX(cols: Column[]): boolean {
  let xCount = 0
  let zCount = 0
  for (const c of cols) {
    if (Math.abs(c.outDirX) > Math.abs(c.outDirZ)) xCount += c.racks.length
    else zCount += c.racks.length
  }
  return xCount > zCount
}

function columnsAsRows(cols: Column[]): Row[] {
  return cols.map((c) => ({
    pos: c.x,
    outSign: c.outDirX > 0 ? 1 : -1,
    perpMin: c.minZ,
    perpMax: c.maxZ,
    racks: c.racks,
  }))
}

/** Racks whose footprint CENTRES sit within 0.8 m in Z are one row. */
function groupRowsByZ(racks: PlanModel[]): Row[] {
  const rows: { pos: number; racks: PlanModel[]; outDirs: number[] }[] = []
  for (const r of racks) {
    const near = applyQuat({ x: 0, y: 0, z: 0 }, r.quaternion)
    const far = applyQuat({ x: r.size.x, y: 0, z: r.size.z }, r.quaternion)
    const zCentre = r.location.z + (near.z + far.z) / 2
    const dir = outletDirection(r.quaternion)
    const hit = rows.find((row) => Math.abs(row.pos - zCentre) < 0.8)
    if (hit) {
      hit.racks.push(r)
      hit.outDirs.push(dir.z)
    } else {
      rows.push({ pos: zCentre, racks: [r], outDirs: [dir.z] })
    }
  }
  rows.sort((a, b) => a.pos - b.pos)
  return rows.map((row) => {
    const avg = row.outDirs.reduce((s, v) => s + v, 0) / row.outDirs.length
    const xs = row.racks.map((r) => r.location.x)
    return {
      pos: row.pos,
      outSign: avg > 0 ? 1 : -1,
      perpMin: Math.min(...xs),
      perpMax: Math.max(...xs) + (row.racks[0]!.size.x || 0.6),
      racks: row.racks,
    }
  })
}

function detectAisles(rows: Row[], facesX: boolean, baffles: PlanModel[]): Aisle[] {
  const out: Aisle[] = []
  for (let i = 0; i < rows.length - 1; i += 1) {
    const a = rows[i]!
    const b = rows[i + 1]!
    if (Math.abs(a.pos - b.pos) > 6) continue
    const hot = a.outSign > 0 && b.outSign < 0
    const cold = a.outSign < 0 && b.outSign > 0
    if (!hot && !cold) continue
    const midPos = (a.pos + b.pos) / 2
    let overlapMin = Math.max(a.perpMin, b.perpMin)
    let overlapMax = Math.min(a.perpMax, b.perpMax)
    if (overlapMax < overlapMin) overlapMax = (a.perpMax + b.perpMax) / 2
    const midPerp = (overlapMin + overlapMax) / 2
    const worldX = facesX ? midPos : midPerp
    const worldZ = facesX ? midPerp : midPos
    const contained = baffles.some(
      (bf) => Math.hypot(bf.location.x - worldX, bf.location.z - worldZ) < 5,
    )
    out.push({ type: hot ? 'HOT' : 'COLD', worldX, worldZ, vertical: !facesX, contained })
  }
  return out
}

function columnLabels(cols: Column[]): ColumnLabel[] {
  return cols.map((col, i) => {
    const top = col.racks.reduce(
      (best, r) => (r.location.z < best.location.z ? r : best),
      col.racks[0]!,
    )
    return {
      text: `C${i + 1}`,
      worldX: col.x + (top.size.x || 0.6) / 2,
      worldZ: top.location.z - 0.3,
    }
  })
}
