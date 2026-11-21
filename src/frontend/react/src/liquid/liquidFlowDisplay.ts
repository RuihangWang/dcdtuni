/**
 * Drives the liquid flow arrows (the shared js/liquid_flow_anim.js) from the
 * shown result.
 *
 * A post-processing helper, following the rule that a scene helper shows on
 * its owning tab only (CLAUDE.md, frontend rule 7): visible on the
 * post-processing tab in liquid mode while the liquid results panel is open,
 * never in rack detail (one rack at the origin, while arrows are in room
 * coordinates), and only when switched on. The viewport ticks them every
 * frame and reports its tab; this module rebuilds them when the result, the
 * shown sample, the display options, the scene or the cabinet display change.
 *
 * Rebuilding touches only the arrows' own instanced mesh: no scene rebuild,
 * no material change, and nothing that enters the Omniverse scene key (the
 * arrows carry `isLiquidFlow`, which the snapshot skips).
 */

import * as THREE from 'three'
import '../../../shared/js/liquid_flow_anim.js'
import { cabinetFrame, type ClosedCabinet } from '../geometry/closedCabinetPipeClip'
import { renderRootOf } from '../geometry/modelOpacity'
import { getRackDetail, subscribeRackDetail } from '../geometry/rackDetail'
import { cabinetIsClosed } from '../legacy/liquidScene'
import * as store from '../projectStore'
import { allModels } from '../schema/cfdproj'
import { subscribeScene } from '../viewportBus'
import { leafDisplayRevision, showCabinetPipes, subscribeClosedCabinetDisplay } from './closedCabinetDisplay'
import { arrowScales, liquidDisplayOptions, subscribeLiquidDisplay } from './liquidDisplayState'
import { buildLiquidArrowSpec, type LiquidArrowSpec } from './liquidFlowScene'
import { isLiquidModeActive, subscribeLiquidMode } from './liquidModeState'
import * as liquid from './liquidSession'
import { selectedResultSample, subscribeResultSample } from './liquidTempColor'

interface LiquidFlowAnimApi {
  rebuild(spec: LiquidArrowSpec, parent: THREE.Object3D): void
  tick(now: number, camera?: { x: number; y: number; z: number }): boolean
  dispose(): void
  setVisible(visible: boolean): void
  setOptions(o: { animate?: boolean; speedScale?: number }): void
  isVisible(): boolean
  count(): number
  instance(i: number): { position: number[]; direction: number[]; scale: number[]; color: number } | null
}

export function liquidFlowAnim(): LiquidFlowAnimApi | null {
  const w = window as unknown as { liquidFlowAnim?: LiquidFlowAnimApi; __liquidFlowSelfDrive?: boolean }
  // The React viewport renders every frame and ticks the arrows itself.
  w.__liquidFlowSelfDrive = false
  return w.liquidFlowAnim ?? null
}

/** Where the arrows may show (the supplyFlowVisible rule, for the liquid results). */
export function liquidFlowVisible(activeTab: string, rackDetailTarget: string | null | undefined,
  liquidMode: boolean, panelOpen: boolean, enabled: boolean): boolean {
  return enabled && panelOpen && liquidMode && activeTab === 'postprocess' && !rackDetailTarget
}

let activeTab = 'geometry'
let panels = 0
let lastKey = ''
let lastSpec: LiquidArrowSpec | null = null
let sceneVersion = 0

function root(): THREE.Object3D | null {
  return ((window as unknown as { globalGroup?: THREE.Object3D }).globalGroup) ?? null
}

function visible(): boolean {
  return liquidFlowVisible(activeTab, getRackDetail().target, isLiquidModeActive(), panels > 0, liquidDisplayOptions().arrows)
}

/** Closed opaque CDU cabinets, in which arrows are not drawn. */
function closedCabinets(): ClosedCabinet[] {
  const project = store.getProject()
  if (!project || showCabinetPipes()) return []
  const out: ClosedCabinet[] = []
  for (const { model } of allModels(project)) {
    if (model.kind !== 'cdu' || !cabinetIsClosed(model, renderRootOf(model.uid))) continue
    const box = cabinetFrame(model.uid, (model.geometry ?? {}) as Parameters<typeof cabinetFrame>[1])
    if (box) out.push(box)
  }
  return out
}
function insideAny(cabinets: readonly ClosedCabinet[], p: readonly number[]): boolean {
  const v = new THREE.Vector3()
  return cabinets.some(c => {
    v.set(p[0]!, p[1]!, p[2]!).applyMatrix4(c.toLocal)
    return v.x >= 0 && v.y >= 0 && v.z >= 0 && v.x <= c.size.x && v.y <= c.size.y && v.z <= c.size.z
  })
}

/** Rebuild the arrows if anything they show changed (cheap when nothing did). */
export function refreshLiquidFlowArrows(force = false): void {
  const anim = liquidFlowAnim()
  if (!anim) return
  const show = visible()
  anim.setVisible(show)
  const raw = liquid.liquidRunState().results as Record<string, unknown> | null
  const parent = root()
  const opts = liquidDisplayOptions()
  if (!show || !raw || !parent || !Array.isArray(raw.cell_edge_ids)) {
    if (!raw || !Array.isArray(raw?.cell_edge_ids)) { anim.dispose(); lastSpec = null; lastKey = '' }
    return
  }
  const sample = selectedResultSample()
  const key = JSON.stringify([String(raw.run_id ?? ''), (raw.time_s as unknown[] | undefined)?.length ?? 0, sample,
    opts.density, opts.arrowColor, opts.scale, opts.arrowManual, sceneVersion, leafDisplayRevision(), parent.uuid])
  if (!force && key === lastKey && anim.count() > 0) {
    anim.setOptions({ animate: opts.animate, speedScale: opts.speedScale })
    return
  }
  lastKey = key
  const cabinets = closedCabinets()
  const spec = buildLiquidArrowSpec(raw, sample, parent, opts, arrowScales(raw, sample, opts), p => insideAny(cabinets, p))
  lastSpec = spec
  anim.rebuild(spec, parent)
  anim.setVisible(show)
}

/** The viewport's tab. */
export function setLiquidFlowTab(tab: string): void {
  if (tab === activeTab) return
  activeTab = tab
  refreshLiquidFlowArrows()
}

const cameraLocal = new THREE.Vector3()
/** Once per rendered frame, from the viewport: arrows ride on the pipe side facing `camera`. */
export function tickLiquidFlow(now: number, camera?: THREE.Camera): void {
  const anim = liquidFlowAnim()
  if (!anim) return
  const parent = root()
  if (camera && parent) {
    camera.getWorldPosition(cameraLocal)
    parent.worldToLocal(cameraLocal)
    anim.tick(now, cameraLocal)
  } else anim.tick(now)
}

/** What is drawn now, for the results panel and the browser check. */
export function liquidFlowSummary(): { visible: boolean; arrows: number; instances: number; kinds: Record<string, number>; skipped: LiquidArrowSpec['skipped'] | null } {
  const anim = liquidFlowAnim()
  const kinds: Record<string, number> = {}
  for (const a of lastSpec?.arrows ?? []) kinds[a.kind] = (kinds[a.kind] ?? 0) + 1
  return { visible: !!anim?.isVisible() && visible(), arrows: lastSpec?.arrows.length ?? 0, instances: anim?.count() ?? 0, kinds, skipped: lastSpec?.skipped ?? null }
}
export function liquidFlowSpec(): LiquidArrowSpec | null { return lastSpec }

/**
 * While a liquid results panel is open: follow everything the arrows depend
 * on. Returns the release; the last release hides the arrows.
 */
export function openLiquidFlowDisplay(): () => void {
  panels++
  const offs = [
    liquid.subscribeLiquid(() => refreshLiquidFlowArrows()),
    subscribeResultSample(() => refreshLiquidFlowArrows()),
    subscribeLiquidDisplay(() => refreshLiquidFlowArrows()),
    subscribeScene(v => { sceneVersion = v; refreshLiquidFlowArrows() }),
    subscribeClosedCabinetDisplay(() => refreshLiquidFlowArrows()),
    subscribeLiquidMode(() => refreshLiquidFlowArrows()),
    subscribeRackDetail(() => refreshLiquidFlowArrows()),
  ]
  refreshLiquidFlowArrows(true)
  // Read-only handles for the browser check (what is drawn, and why).
  const w = window as unknown as { __liquidFlowSummary?: typeof liquidFlowSummary; __liquidFlowSpec?: typeof liquidFlowSpec }
  w.__liquidFlowSummary = liquidFlowSummary
  w.__liquidFlowSpec = liquidFlowSpec
  return () => {
    offs.forEach(off => off())
    panels = Math.max(0, panels - 1)
    if (!panels) liquidFlowAnim()?.setVisible(false)
  }
}
