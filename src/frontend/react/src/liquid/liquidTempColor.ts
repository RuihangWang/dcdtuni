/**
 * Colouring the 3D scene from a finished liquid solve.
 *
 * Two independent switches, both from the legacy results panel's `🎨 3D Display`
 * block (js/liquid_results_panel.js:129-138):
 *
 *   PIPES   `colorPipesByTemperature` (js/liquid_pipe_renderer.js:845) averages
 *           `cell_temp_K` over each pipe mesh. That function is REUSED here: it
 *           reaches the pipe meshes through `_pipeGroup`, a closure this app has
 *           no other way into, and it also hides the waypoint spheres and saves
 *           the materials it overwrites so the toggle is reversible.
 *
 *   CHIPS   `_toggleServerTemperatureColoring` (:931) is NOT reused. It matches
 *           meshes with `child.userData.modelId === rackModel.id`, and a V4
 *           construction has no `id` — the key is `uid`, which is what
 *           `geometry_scene.js:2401` writes into `userData.modelId`. So the
 *           legacy comparison is `undefined === <uid>` for every mesh and the
 *           checkbox does nothing in EITHER app. Ported here against `uid`.
 *
 * ## Why the result document is republished onto the project
 *
 * The pipe path reads `window.state.liquidResults`, and in this app
 * `window.state` is a getter for the live project (legacy/liquidScene.ts). The
 * result is therefore defined onto that object — NON-ENUMERABLE, so it is
 * invisible to `JSON.stringify` and to `structuredClone`, and can never reach a
 * saved .cfdproj or an undo snapshot. It is re-published before every call
 * because React replaces the project object on each edit.
 */

import type * as THREE from 'three'
import { colormapData } from '../postprocess/ppColormaps'
import * as store from '../projectStore'
import * as liquid from './liquidSession'
import { MeshColorOverlay } from './meshColorOverlay'
import { bindingEdges } from './liquidFlow'
import { chipScale, networkScale, subscribeLiquidDisplay } from './liquidDisplayState'
import { paintLiquidNetwork } from './liquidNetworkPaint'
import { subscribeScene } from '../viewportBus'
import { subscribeClosedCabinetDisplay } from './closedCabinetDisplay'
import {
  CMAP, liquidFrame, tempToColor, valueRange, type RackSummaryNames,
} from './liquidColourRules'
export {
  liquidFrame, liquidGlobalDomains, tempToColor, valueRange,
  type LiquidFrameColours, type LiquidTarget, type LiquidTargetKind, type RackSummaryNames,
} from './liquidColourRules'


// ---------------------------------------------------------------------------
// pure
// ---------------------------------------------------------------------------

/**
 * The steady value of every row of a `[component][time]` matrix.
 *
 * A row that is a bare number rather than a series is taken as-is, which is how
 * `colorPipesByTemperature` reads `cell_temp_K` — an older result wrote one
 * scalar per cell.
 */
export function lastStepValues(rows: unknown): number[] {
  if (!Array.isArray(rows)) return []
  const out: number[] = []
  for (const row of rows) {
    if (Array.isArray(row)) {
      const last = row[row.length - 1]
      if (typeof last === 'number') out.push(last)
    } else if (typeof row === 'number') {
      out.push(row)
    }
  }
  return out
}

export interface ColorableServer {
  uid: string
  name: string
}

export interface ColorableRack {
  uid: string
  name: string
  servers: ColorableServer[]
}

export interface ChipColorTarget {
  modelUid: string
  equipmentUid: string
  color: number
}

/**
 * Which mesh gets which colour, walking the result's racks in order.
 *
 * The chip temperatures are a FLAT list indexed by the position of the cold
 * plate across every server of every rack, so the walk order is the mapping —
 * there is no id to join on. A rack the project does not have skips its servers
 * without consuming their temperatures, which is the legacy's own behaviour and
 * the reason a renamed rack shifts the colours of every rack after it.
 */
export function chipColorTargets(
  rackSummaries: RackSummaryNames[],
  chipTempsK: number[],
  racks: ColorableRack[],
  lut: Uint8Array,
): ChipColorTarget[] {
  const range = valueRange(chipTempsK)
  if (!range) return []
  const out: ChipColorTarget[] = []
  let cpIdx = 0
  for (const summary of rackSummaries) {
    const rack = racks.find((r) => r.uid === summary.rack_id) ?? racks.find((r) => r.name === summary.rack_id)
    for (const srv of summary.servers) {
      const count = srv.cp_edge_ids?.length || 1
      const temperatures = chipTempsK.slice(cpIdx, cpIdx + count)
      cpIdx += count
      const server = rack?.servers.find((s) => s.uid === srv.server_id) ?? rack?.servers.find((s) => s.name === srv.server_id)
      if (!rack || !server || temperatures.length !== count) continue
      const T = Math.max(...temperatures)
      out.push({
        modelUid: rack.uid,
        equipmentUid: server.uid,
        color: tempToColor(T, range.min, range.max, lut),
      })
    }
  }
  return out
}

/** The racks and their servers, as `chipColorTargets` needs them. */
export function colorableRacks(models: { uid: string; kind?: string; meta?: { name?: string } }[]): ColorableRack[] {
  const out: ColorableRack[] = []
  for (const model of models) {
    if (model.kind !== 'rack') continue
    const servers = (model as unknown as { constructions?: { servers?: Record<string, unknown> } })
      .constructions?.servers
    const entries = servers ? Object.entries(servers) : []
    out.push({
      uid: model.uid,
      name: String(model.meta?.name ?? model.uid),
      servers: entries.map(([uid, srv]) => ({
        uid,
        name: String((srv as { meta?: { name?: string } })?.meta?.name ?? uid),
      })),
    })
  }
  return out
}

// ---------------------------------------------------------------------------
// scene
// ---------------------------------------------------------------------------

type Bag = Record<string, unknown>

/** Object-mean pipe temperature, joined through cell/edge IDs, never mesh order. */
export function mappedPipeTemperatures(raw: Bag, sample = -1): Map<string,number> {
  const ids=Array.isArray(raw.cell_edge_ids)?raw.cell_edge_ids:[]
  const rows=Array.isArray(raw.cell_temp_K)?raw.cell_temp_K:[]
  const byEdge=new Map<string,number[]>()
  ids.forEach((id,i)=>{
    const row=rows[i]
    const value=Array.isArray(row)?row[sample<0?row.length-1:sample]:row
    if(typeof id!=='string'||typeof value!=='number'||!Number.isFinite(value))return
    byEdge.set(id,[...(byEdge.get(id)??[]),value])
  })
  const out=new Map<string,number>()
  const bindings=raw.object_bindings && typeof raw.object_bindings==='object'?raw.object_bindings as Bag:{}
  const known=new Set((Array.isArray(raw.edge_ids)?raw.edge_ids:[]).map(String))
  for(const [uid,binding] of Object.entries(bindings)) {
    if(!binding||typeof binding!=='object')continue
    const edges=bindingEdges(uid,binding as Bag,known)
    const values=edges.flatMap(id=>byEdge.get(String(id))??[])
    if(values.length)out.set(uid,values.reduce((a,b)=>a+b,0)/values.length)
  }
  return out
}

let resultSample = -1
let renderRevision = 0
const sampleListeners=new Set<()=>void>()
export function subscribeResultSample(fn:()=>void) {sampleListeners.add(fn);return ()=>{sampleListeners.delete(fn)}}
export function selectedResultSample() { return resultSample }
/** The un-painted material of a mesh the result overlays recoloured. While
 * Omniverse colours liquid results itself, the scene snapshot exports these
 * originals so a resync never bakes a Three.js result frame into OV's base
 * materials. */
export function resultOriginalMaterial(mesh: THREE.Mesh): THREE.Material | THREE.Material[] | undefined {
  return pipeOverlay.original(mesh) ?? chipOverlay.original(mesh)
}
/** Includes material-only changes at the same physical result time. */
export function resultRenderRevision() { return renderRevision }
function publishRenderChange() {
  renderRevision++
  sampleListeners.forEach(fn=>fn())
}
export function selectedResultTime(): number | null {
  const times=(liquid.liquidRunState().results as Bag|null)?.time_s
  if(!Array.isArray(times))return null
  return times[resultSample<0?times.length-1:resultSample]??null
}
export function setResultSample(sample:number) {
  resultSample=sample
  if(pipesOn)setPipeTempColoring(true)
  if(chipsOn)setChipTempColoring(true)
  publishRenderChange()
}

function win(): Bag {
  return window as unknown as Bag
}

/**
 * `_ppColormapPresets`, which the legacy pipe path samples for its colours.
 *
 * The legacy page defines it; this app does not, and without it
 * `_tempToColor` silently falls back to a hard-coded blue-to-red ramp — the
 * pipes would be a different colour in the two apps for the same temperature.
 * Never overwrites an existing one.
 */
function ensureColormapPresets() {
  const w = win()
  if (w._ppColormapPresets) return
  w._ppColormapPresets = {
    coolwarm: () => colormapData('coolwarm'),
    jet: () => colormapData('jet'),
    viridis: () => colormapData('viridis'),
  }
}

/** Publish the RAW result document where the legacy pipe path looks for it. */
function exposeResults(): boolean {
  const project = store.getProject() as unknown as Bag | null
  const results = liquid.liquidRunState().results
  if (!project || !results) return false
  Object.defineProperty(project, 'liquidResults', {
    value: results,
    configurable: true,
    enumerable: false,
    writable: true,
  })
  return true
}

let pipesOn = false
let chipsOn = false

export function isPipeColoringOn(): boolean {
  return pipesOn
}

export function isChipColoringOn(): boolean {
  return chipsOn
}

/** Paint again what is switched on (new meshes, options or display geometry). */
export function reapplyResultColouring(): void {
  if (pipesOn) setPipeTempColoring(true)
  if (chipsOn) setChipTempColoring(true)
}
let repaintInstalled: (() => void) | null = null
/**
 * Keep the colouring on the scene while a liquid results panel is open: a
 * scene rebuild replaces the meshes, a closed-cabinet display refresh replaces
 * the tubes' geometry (and with it their vertex colours), and a display
 * option changes the colours. Idempotent; returns the release.
 */
export function installResultRepaint(): () => void {
  if (!repaintInstalled) {
    const offs = [subscribeLiquidDisplay(reapplyResultColouring), subscribeScene(reapplyResultColouring),
      subscribeClosedCabinetDisplay(reapplyResultColouring)]
    repaintInstalled = () => { offs.forEach(off => off()); repaintInstalled = null }
  }
  return repaintInstalled
}

/** Forget the toggles when the workspace is replaced; the meshes went with it. */
export function resetTempColoring() {
  pipesOn = false
  chipsOn = false
  chipOverlay.restore()
  pipeOverlay.restore()
  resultSample=-1
}

const pipeOverlay = new MeshColorOverlay()
export function setPipeTempColoring(on: boolean): boolean {
  const raw=liquid.liquidRunState().results as Bag|null
  // Dynamic results carry a physical mapping. Do not pass them through the
  // legacy mesh-order approximation, which could paint a different pipe.
  if(raw?.cell_edge_ids || pipeOverlay.size) {
    const root=globalGroup()
    if(!root)return false
    if(!on){pipeOverlay.restore();pipesOn=false;publishRenderChange();return true}
    // The whole water network on ONE scale — pipes, rack manifolds and their
    // stubs, tees, flow valves, the CDU — by the rules Omniverse applies too,
    // in the chosen quantity, each tube coloured along its length.
    const before=pipeOverlay.size
    const painted=raw?paintLiquidNetwork(root,raw,resultSample,networkScale(raw,resultSample),pipeOverlay):0
    if(!painted&&before)pipeOverlay.restore()
    pipesOn=on&&painted>0
    if(painted>0||before)publishRenderChange()
    return !on||painted>0
  }
  const toggle = win()._togglePipeTemperatureColoring as ((v: boolean) => void) | undefined
  if (!toggle) return false
  if (on) {
    ensureColormapPresets()
    if (!exposeResults()) return false
  }
  toggle(on)
  pipesOn = on
  publishRenderChange()
  return true
}

const chipOverlay = new MeshColorOverlay()

function globalGroup(): THREE.Object3D | null {
  return (win().globalGroup as THREE.Object3D | undefined) ?? null
}

/**
 * Paint (or restore) the server bodies from `chip_temp_K`.
 *
 * Restoring reads the saved hex rather than rebuilding the scene: a rebuild
 * would drop the selection and re-run every rack's CSG for a colour change.
 */
export function setChipTempColoring(on: boolean): boolean {
  const root = globalGroup()
  if (!root) return false

  if (!on) {
    chipOverlay.restore()
    chipsOn = false
    publishRenderChange()
    return true
  }

  const raw = liquid.liquidRunState().results as Bag | null
  if (!raw) return false
  // Bound (DAE) results join chips by cold-plate edge id — the rule Omniverse
  // uses; only legacy results fall back to the walk-order mapping below.
  const frame = raw.object_bindings ? liquidFrame(raw, resultSample) : null
  if (frame) {
    // On the display scale (whole run by default), like the network.
    const scale = chipScale(raw, resultSample)
    const lut = colormapData(CMAP)
    const byServer = new Map(frame.targets.filter(t => t.kind === 'server').map(t =>
      [`${t.modelUid}|${t.equipmentUid}`, scale ? tempToColor(t.T, scale[0], scale[1], lut) : t.color]))
    let painted = 0
    root.traverse((child) => {
      const mesh = child as THREE.Mesh & { isMesh?: boolean }
      const data = child.userData as { modelId?: string; equipmentId?: string }
      if (!mesh.isMesh || !data.modelId || !data.equipmentId) return
      const color = byServer.get(`${data.modelId}|${data.equipmentId}`)
      const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
      if (color == null || !materials.some(m => (m as THREE.MeshStandardMaterial).color)) return
      chipOverlay.paint(mesh, color)
      painted++
    })
    chipsOn = painted > 0
    if (painted > 0) publishRenderChange()
    return painted > 0
  }
  const chips = resultSample<0?lastStepValues(raw.chip_temp_K):
    (Array.isArray(raw.chip_temp_K)?raw.chip_temp_K.map(row=>Array.isArray(row)?row[resultSample]:row):[])
  if (chips.length === 0) return false
  const summaries = Array.isArray(raw.rack_summaries)
    ? (raw.rack_summaries as RackSummaryNames[])
    : []
  // `allModels` is the shim legacy/liquidScene.ts installs, so this reads the
  // same model list the scene was built from rather than a second traversal.
  const models = (win().allModels as (() => { uid: string }[]) | undefined)?.() ?? []
  const targets = chipColorTargets(
    summaries,
    chips,
    colorableRacks(models as { uid: string; kind?: string; meta?: { name?: string } }[]),
    colormapData(CMAP),
  )
  if (targets.length === 0) return false

  const byModel = new Map<string, Map<string, number>>()
  for (const target of targets) {
    let inner = byModel.get(target.modelUid)
    if (!inner) {
      inner = new Map()
      byModel.set(target.modelUid, inner)
    }
    inner.set(target.equipmentUid, target.color)
  }

  let painted=0
  root.traverse((child) => {
    const mesh = child as THREE.Mesh & { isMesh?: boolean }
    if (!mesh.isMesh) return
    const data = child.userData as { modelId?: string; equipmentId?: string }
    if (!data.modelId || !data.equipmentId) return
    const color = byModel.get(data.modelId)?.get(data.equipmentId)
    if (color == null) return
    const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material]
    if(!materials.some(m=>(m as THREE.MeshStandardMaterial).color))return
    chipOverlay.paint(mesh,color)
    painted++
  })
  chipsOn = painted>0
  if(painted>0)publishRenderChange()
  return painted>0
}
