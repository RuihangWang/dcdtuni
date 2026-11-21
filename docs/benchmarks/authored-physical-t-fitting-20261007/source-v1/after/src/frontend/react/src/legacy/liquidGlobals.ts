import {createAuthoredTBranch, DEFAULT_T_BRANCH_DIAMETER_M} from '../liquid/tBranch'
/**
 * The bare globals `js/liquid_mode.js` needs and `liquidScene.ts` did not have.
 *
 * `liquidScene.ts` shims the SCENE side — `globalGroup`, `camera`, `state`,
 * `_modelMap`. This file shims the EDIT side: the four names the pipe editor
 * calls to create, add, remove and select models, plus the rack-manifold
 * geometry it snaps pipe ends to.
 *
 * Why that split matters is what the two modules do when a name is missing.
 * `window.addModel` and `window.removeModelByUid` are called UNGUARDED
 * (liquid_mode.js:202, :1023, :1186, :1190-1192), so upgrading a pipe to a
 * T-branch and merging two pipes threw `window.addModel is not a function` and
 * left the project half-edited — the original pipe already removed. The rest
 * are read through `typeof x === 'function'` guards, so their absence is
 * SILENT: every rack-manifold snap target evaporated because
 * `_getManifoldEndpoint` was undefined (liquid_mode.js:699), and a pipe end
 * dragged onto a manifold simply did not stick.
 *
 * ## These write in place, and that is deliberate
 *
 * The legacy `addModel`/`removeModelByUid` mutate the one live project. So do
 * these, through `store.mutateLive`, because every caller already bracketed the
 * whole operation with its own `pushUndoCheckpoint(...)` — `_upgradeToTBranch`
 * removes one pipe and adds three models, and routing each through `store.edit`
 * would make four undo entries out of one user action and clone the project
 * four times over.
 */

import * as THREE from 'three'
import type { CfdProject, Construction, Vec3 } from '../schema/cfdproj'
import * as store from '../projectStore'
import { currentSelectedUid, requestSelect } from '../interaction/selectionBridge'
import { isRackDetailMode } from '../geometry/rackDetail'
import { finalizeNewModel, rackState, stateV4, type V3Seed } from './cfdprojModules'
import { stlNaming } from './stlNaming'
import { geometryPure } from './geometryBuilder'

type Bag = Record<string, unknown>

interface StateV4Writes {
  addConstruction(state: unknown, model: Construction): Construction
  removeConstruction(state: unknown, uid: string): boolean
}

/** `cfdproj_state_v4.js` exports these; the shared typing only lists the readers. */
function v4writes(): StateV4Writes {
  return stateV4() as unknown as StateV4Writes
}

interface RackStateExtras {
  createFacesState(labelOverrides?: Record<string, string>): unknown
  getDefaultModelTransform(): unknown
  getDefaultModelMoveTool(): unknown
  getDefaultCfd(kind: string): unknown
}

function rackExtras(): RackStateExtras {
  return rackState() as unknown as RackStateExtras
}

// ---------------------------------------------------------------------------
// uids
// ---------------------------------------------------------------------------

/**
 * The highest `m_<n>` already issued in this session.
 *
 * A project scan alone is not enough. `_upgradeToTBranch` builds a junction and
 * two pipes BEFORE adding any of them (liquid_mode.js:1139-1188), so three
 * consecutive scans of the same unchanged project return the same uid and the
 * three models collide into one bucket entry — two of them silently vanish.
 * The legacy page has no such bug because its `nextModelId` is a counter.
 */
let issued = 0

export function nextLiquidUid(project: CfdProject): string {
  let max = 0
  for (const items of Object.values(project.constructions ?? {})) {
    if (!items) continue
    for (const uid of Object.keys(items)) {
      const m = /^m_(\d+)$/.exec(uid)
      if (m) max = Math.max(max, Number(m[1]))
    }
  }
  const n = Math.max(max + 1, issued + 1)
  issued = n
  return `m_${n}`
}

/** Test seam: forget what this session has handed out. */
export function _resetLiquidUidCounter() {
  issued = 0
}

// ---------------------------------------------------------------------------
// factories — `createPipeSegmentModel` / `createPipeJunctionModel`
// ---------------------------------------------------------------------------

/**
 * The seed `createPipeSegmentModel` (STL_generate.html:2948) builds.
 *
 * Note the 25 mm default and the EMPTY waypoint list: this is not the free-pipe
 * factory in `legacy/factories.ts`, which is the legacy `createFreePipe` — that
 * one overrides the spec to 100 mm and seeds three waypoints. Both callers here
 * assign their own waypoints straight afterwards, so seeding any would be
 * overwritten, and a 100 mm default would silently re-spec a pipe that was
 * split out of a 25 mm one.
 */
export function pipeSegmentSeed(
  project: CfdProject,
  name: string,
  startPortId: string,
  endPortId: string,
  color: string,
): V3Seed {
  const rack = rackExtras()
  return {
    id: nextLiquidUid(project),
    kind: 'pipe_segment',
    name,
    enabled: true,
    showAnchorMarker: false,
    opacity: 1,
    anchor: { x: 0, y: 0, z: 0 },
    base: { W: 0, D: 0, H: 0 },
    transform: rack.getDefaultModelTransform(),
    moveTool: rack.getDefaultModelMoveTool(),
    faces: rack.createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: rack.getDefaultCfd('pipe_segment'),
    pipe: {
      spec: 'pipe_25mm',
      diameter: 0.025,
      startPortId,
      endPortId,
      waypoints: [],
      color,
      junctions: [],
    },
  }
}

/** The seed `createPipeJunctionModel` (STL_generate.html:3000) builds. */
export function pipeJunctionSeed(
  project: CfdProject,
  name: string,
  parentPipeId: string | null,
  position: Vec3 | null,
  fittingDiameterM = DEFAULT_T_BRANCH_DIAMETER_M,
): V3Seed {
  const junction = createAuthoredTBranch(fittingDiameterM)
  const rack = rackExtras()
  return {
    id: nextLiquidUid(project),
    kind: 'pipe_junction',
    name,
    enabled: true,
    showAnchorMarker: true,
    opacity: 1,
    anchor: position ? { ...position } : { x: 0, y: 0, z: 0 },
    base: { W: 0.06, D: 0.06, H: 0.06 },
    transform: rack.getDefaultModelTransform(),
    moveTool: rack.getDefaultModelMoveTool(),
    faces: rack.createFacesState(),
    solidRegions: [],
    customSTLBase64: null,
    customOffset: { x: 0, y: 0, z: 0 },
    cfd: rack.getDefaultCfd('pipe_junction'),
    parentPipeId,
    junction,
  }
}

// ---------------------------------------------------------------------------
// rack manifold geometry — `_getManifoldEndpoint`
// ---------------------------------------------------------------------------

export type ManifoldPipeType = 'supply' | 'return'
export type ManifoldEnd = 'top' | 'bottom'

/**
 * Where a rack's supply or return manifold starts and ends, in world metres.
 *
 * A port of `window._getManifoldEndpoint` (STL_generate.html:7142). It is the
 * ONLY source of the rack-side snap targets — `_collectSnapTargets`
 * (liquid_mode.js:699) asks it for four points per liquid rack — and the
 * "Connect Valve to Manifold" button (liquid_mode.js:1651) places the valve on
 * the point it returns. Without it a liquid rack contributes no snap target at
 * all and both interactions no-op without complaint.
 *
 * Returns a plain vector rather than a `THREE.Vector3` so the arithmetic can be
 * tested without a scene; the window shim wraps it, because the callers do
 * `pos.distanceTo(...)` and `.clone()` on the result.
 *
 * `cooling` is read defensively where the legacy reads it bare: a rack without
 * one throws there, and a throw inside `_collectSnapTargets` takes out snapping
 * for EVERY model, not just that rack.
 */
export function manifoldEndpoint(
  rack: Construction | null | undefined,
  pipeType: ManifoldPipeType,
  end: ManifoldEnd,
): Vec3 | null {
  if (!rack || rack.kind !== 'rack') return null
  const cooling = ((rack as unknown as Bag).cooling ?? {}) as Bag
  if (cooling.type !== 'liquid') return null
  const geometry = (rack.geometry ?? {}) as Bag
  const slotHeight = Number(geometry.slot ?? 0) * Number(geometry.slotHeight ?? 0)
  // A rack with no U-slots has no manifold to hang a pipe on. The legacy bails
  // here for the same reason, and returning (0,0,0) instead would put four snap
  // targets at the world origin.
  if (!(slotHeight >= 1e-6)) return null

  // Render, snapping and graph compilation share this one layout + transform.
  const frame = geometryPure().liquidEquipmentPortFrame(rack, pipeType, end)
  return frame
    ? { x: frame.position.x, y: frame.position.y, z: frame.position.z }
    : null
}

// ---------------------------------------------------------------------------
// installation
// ---------------------------------------------------------------------------

function project(): CfdProject | null {
  return store.getProject()
}

/**
 * Put the edit-side globals on `window`.
 *
 * Called from `installLiquidScene`, before the modules are imported, so nothing
 * they run at load time can see a half-built set.
 */
export function installLiquidGlobals() {
  const w = window as unknown as Record<string, unknown>

  w.addModel = (model: Construction) => {
    store.mutateLive((p) => {
      v4writes().addConstruction(p, model)
    })
    return model
  }
  w.removeModelByUid = (uid: string) => {
    // `_mergePipes` passes `otherPipe.id`, which V4 models do not have — a
    // pre-V4 leftover in the shared module. Guarding here keeps that call a
    // no-op instead of deleting whatever `undefined` happens to key.
    if (!uid) return
    store.mutateLive((p) => {
      v4writes().removeConstruction(p, uid)
    })
  }

  w.createPipeSegmentModel = (
    name = 'Pipe',
    startPortId = '',
    endPortId = '',
    color = 'supply',
  ) => {
    const p = project()
    if (!p) return null
    return finalizeNewModel(pipeSegmentSeed(p, name, startPortId, endPortId, color))
  }
  w.createPipeJunctionModel = (
    name = 'T-Branch',
    parentPipeId: string | null = null,
    position: Vec3 | null = null,
    fittingDiameterM = DEFAULT_T_BRANCH_DIAMETER_M,
  ) => {
    const p = project()
    if (!p) return null
    return finalizeNewModel(pipeJunctionSeed(p, name, parentPipeId, position, fittingDiameterM))
  }

  w._getManifoldEndpoint = (
    rack: Construction,
    pipeType: ManifoldPipeType,
    end: ManifoldEnd,
  ) => {
    const v = manifoldEndpoint(rack, pipeType, end)
    return v ? new THREE.Vector3(v.x, v.y, v.z) : null
  }

  // The legacy pair. `rebuildGeometry` rebuilds the scene and `updateUI`
  // repaints the panels; in this app both are the same notification, because
  // the scene is rebuilt from the project by the render that the notification
  // triggers.
  w.rebuildGeometry = () => store.notifyLiveEdit()

  // `setSelection(type, modelId)` — only ever called with 'model' by these two
  // modules, so the type is accepted and ignored rather than mapped onto a
  // selection vocabulary this app does not share.
  w.setSelection = (_type: string, modelId: string | null) => requestSelect(modelId ?? null)
  w.getSelectedModel = () => {
    const uid = currentSelectedUid()
    const p = project()
    if (!uid || !p) return null
    return store.findConstruction(p, uid)?.model ?? null
  }

  // Read by the pipe renderer (liquid_pipe_renderer.js:612) and the click
  // handler (liquid_mode.js:1531) to stand down while a rack is opened up: pipe
  // control points drawn over the rack-detail view are handles on geometry that
  // is not on screen.
  w.isRackDetailMode = () => isRackDetailMode()

  /**
   * Select the valve or T-junction `valveId` on pipe `pipeUid`.
   *
   * A port of `window._selectFittingById` (STL_generate.html:12908), same name
   * because the feature-tree row is what calls it. The delay is the legacy's
   * and is load-bearing: selecting the pipe rebuilds its geometry, and a
   * traverse that ran before the rebuild would find the OLD valve group and
   * highlight a mesh that is about to be thrown away.
   */
  w._selectFittingById = (pipeUid: string, valveId: string) => {
    requestSelect(pipeUid)
    setTimeout(() => {
      const group = w.globalGroup as THREE.Object3D | undefined
      const mode = w._liquidMode as
        | { _selectValveByMesh?: (mesh: THREE.Object3D) => void }
        | undefined
      if (!group || !mode?._selectValveByMesh) return
      group.traverse((o) => {
        const ud = o.userData as { isValve?: boolean; valveId?: string } | undefined
        if (ud?.isValve && ud.valveId === valveId) mode._selectValveByMesh?.(o)
      })
    }, 150)
  }

  // `js/liquid_cooling.js:90` names every rack in the .liquidrun through this.
  // Its fallback is a DIFFERENT regex from the shared one: `[^A-Za-z0-9_]`
  // replaces each disallowed character, where `stl_naming`'s `[^a-zA-Z0-9_]+`
  // collapses a RUN of them into one underscore. So without the shim a rack
  // named "Row A - 1" compiles to `Row_A___1` here and `Row_A_1` in the legacy
  // app — two networks whose node ids do not match.
  w.sanitizeName = (text: string | null | undefined) => stlNaming().sanitizeName(text)
}
