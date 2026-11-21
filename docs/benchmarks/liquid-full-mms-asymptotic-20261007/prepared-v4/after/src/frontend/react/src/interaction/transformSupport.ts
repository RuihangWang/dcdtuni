/**
 * Which transform controls an object offers, and how much of them a panel shows.
 *
 * ONE predicate, read by both the inspector's transform block (which rows to
 * draw) and the R / V shortcuts (whether a key does anything). Deriving the
 * rule twice — once from the inspector's branch order, once in the key handler
 * — is how a key ends up switching on rings the panel would never offer.
 *
 * Per kind (`transformSupport`):
 *
 * - **Everything box-like** (rack, ACU, box, wall, baffle, floor, partition
 *   wall, imported STL, CDU, heat source…): the move arrows, the rotate rings,
 *   the anchor X/Y/Z boxes and the yaw/pitch/roll boxes. The CDU gets the
 *   boxes too: the legacy CDU panel opens with the same `anchorControlsForModel`
 *   block as every other model (STL_generate.html:9290), and the React port
 *   had dropped them.
 * - **Air pipe** (`pipe`): both gizmos — `pipeLocalToWorld` applies the
 *   quaternion, so the rings really turn it — but no anchor or angle boxes. Its
 *   shape is the waypoint list, which the panel edits in world coordinates.
 * - **Liquid pipe** (`pipe_segment`): nothing. Its shape is world waypoints,
 *   the renderer ignores `geometry.quaternion`, and the move pivot comes from a
 *   zero-size OBB at the anchor, far from the pipe. The legacy panel has no
 *   transform group either (STL_generate.html:9320).
 * - **T-junction** (`pipe_junction`): move and position, but NO rings and no
 *   angle boxes. Its orientation is `cooling.quaternion`
 *   (liquid_pipe_renderer.js:321), and both the ring gizmo and the angle boxes
 *   write `geometry.quaternion` — they visibly did nothing. Its real rotate is
 *   the liquid-mode Z-only gizmo attached on selection.
 *
 * Per panel (`transformScopeForPanel`):
 *
 * - `full` — the panel IS the object: the whole block.
 * - `part` — the panel is a PART of the object's own body (a face of a box, a
 *   rack's U-slot, a region of a split wall): the SAME whole block, acting on
 *   the whole model, plus one last line saying so. A viewport click on a box,
 *   an ACU, a CDU or a rack front selects such a part, while one on a wall
 *   selects the wall — so a block trimmed down for parts would give the kinds
 *   the user listed different rows after the same gesture, and leave their
 *   numbers one tree click away.
 * - `none` — a patch (the boundary condition), or a separate thing inside the
 *   model (a server, a rack partition): no transform block, and R / V do
 *   nothing there.
 */

import type { Construction } from '../schema/cfdproj'
import { inspectorPanelFor, type InspectorPanel } from './inspectorPanel'
import type { TreeSelection } from './treeNodes'

export interface TransformSupport {
  /** The move arrows (`moveTool`). */
  move: boolean
  /** The rotate rings (`rotateTool`). */
  rotate: boolean
  /** Anchor X / Y / Z boxes in the panel. */
  position: boolean
  /** Yaw / pitch / roll boxes in the panel. */
  rotation: boolean
}

const ALL: TransformSupport = { move: true, rotate: true, position: true, rotation: true }

const BY_KIND: Readonly<Record<string, TransformSupport>> = {
  pipe: { move: true, rotate: true, position: false, rotation: false },
  pipe_segment: { move: false, rotate: false, position: false, rotation: false },
  pipe_junction: { move: true, rotate: false, position: true, rotation: false },
}

export function transformSupport(model: Construction): TransformSupport {
  return { ...(BY_KIND[String(model.kind ?? '')] ?? ALL) }
}

export type TransformScope = 'full' | 'part' | 'none'

const SCOPE: Readonly<Record<InspectorPanel, TransformScope>> = {
  model: 'full',
  planarFace: 'full',
  cdu: 'full',
  airPipe: 'full',
  pipeSegment: 'full',
  // The legacy region panel opens with `anchorControlsForModel`
  // (STL_generate.html:12757): an imported mesh is clicked through one of its
  // solids, so that panel is where the model is moved from.
  region: 'full',
  face: 'part',
  rackSlot: 'part',
  wallRegion: 'part',
  patch: 'none',
  customPatch: 'none',
  equipment: 'none',
  serverFace: 'none',
  partition: 'none',
}

export function transformScopeForPanel(panel: InspectorPanel): TransformScope {
  return SCOPE[panel]
}

export interface TransformTarget {
  scope: Exclude<TransformScope, 'none'>
  support: TransformSupport
}

/**
 * What the transform block shows for this selection — null when it shows none.
 *
 * Null too for a kind with neither gizmo (a liquid pipe): the block has
 * nothing to put at the top, and a key has nothing to switch on.
 */
export function transformTarget(
  model: Construction | null | undefined,
  node?: TreeSelection | null,
): TransformTarget | null {
  if (!model) return null
  const scope = transformScopeForPanel(inspectorPanelFor(model, node))
  if (scope === 'none') return null
  const support = transformSupport(model)
  if (!support.move && !support.rotate) return null
  return { scope, support }
}
