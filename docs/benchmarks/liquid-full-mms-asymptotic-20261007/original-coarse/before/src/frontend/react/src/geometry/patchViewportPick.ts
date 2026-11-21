/**
 * Clicking a surface in the viewport selects its row in the patch list.
 *
 * A port of `ensureViewportClickHandler` (STL_generate.html:24766). The
 * behaviour that matters is the pair of rules it encodes:
 *
 *   - A CLICK is a mousedown and a mouseup within 3 px. OrbitControls drives the
 *     camera from its own pointer events on the same canvas, so without the
 *     threshold every orbit would end by re-selecting whatever was under the
 *     cursor when the drag stopped.
 *   - Only patches that are currently DRAWN are pickable. `THREE.Raycaster`
 *     ignores `.visible`, so an unchecked wall between the camera and the target
 *     would otherwise win the hit and select a patch that is not on screen.
 *     Enforcing it is each source's job, in `meshes()`.
 *
 * The legacy routes one handler by "which panel is active" and lets mesh preview
 * win. Here each panel REGISTERS itself while it is mounted and says how it
 * picks; the highest priority active source wins, which is the same rule
 * without a global mode flag.
 */

import * as THREE from 'three'
import type { SelectionModel } from '../legacy/patchListModules'
import { getVisibleOrderedNames, rowFor } from './patchListDom'
import { subscribeViewport } from '../viewportBus'

const DRAG_THRESHOLD_PX = 3

export interface PatchPickSource {
  /** Mesh preview overlays the post-processing visuals, so it picks first. */
  priority: number
  /** Only pickable while its viewer actually has geometry up. */
  active(): boolean
  /** The objects to raycast — DRAWN patches only. */
  meshes(): THREE.Object3D[]
  resolve(hit: THREE.Intersection): string | null
  container(): HTMLElement | null
  model(): SelectionModel | null
}

const sources = new Set<PatchPickSource>()
let detach: (() => void) | null = null
let unsubscribe: (() => void) | null = null

function bestSource(): PatchPickSource | null {
  let best: PatchPickSource | null = null
  for (const s of sources) {
    if (!s.active()) continue
    if (!best || s.priority > best.priority) best = s
  }
  return best
}

const raycaster = new THREE.Raycaster()

function onClickAt(canvas: HTMLElement, event: MouseEvent) {
  const source = bestSource()
  if (!source) return
  const model = source.model()
  const container = source.container()
  const camera = getCamera()
  if (!model || !container || !camera) return

  const rect = canvas.getBoundingClientRect()
  const ndc = new THREE.Vector2(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    -((event.clientY - rect.top) / rect.height) * 2 + 1,
  )
  raycaster.setFromCamera(ndc, camera)

  const hit = raycaster.intersectObjects(source.meshes(), false)[0]
  if (!hit) {
    // A plain click on empty space clears; a modified one is the user adding to
    // a selection and missing, which must not throw the selection away.
    if (!event.shiftKey && !(event.ctrlKey || event.metaKey)) model.clear()
    return
  }
  const name = source.resolve(hit)
  if (!name) return

  model.handleRowClick(name, {
    shift: event.shiftKey,
    ctrl: event.ctrlKey || event.metaKey,
    orderedNames: getVisibleOrderedNames(container),
  })

  const row = rowFor(container, name)
  if (row && !row.classList.contains('filter-hidden')) {
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
}

let camera: THREE.PerspectiveCamera | null = null
function getCamera() {
  return camera
}

function attach(canvas: HTMLElement) {
  let downX = 0
  let downY = 0
  let downInCanvas = false

  const onDown = (e: MouseEvent) => {
    downX = e.clientX
    downY = e.clientY
    downInCanvas = true
  }
  const onUp = (e: MouseEvent) => {
    const started = downInCanvas
    downInCanvas = false
    if (!started) return
    if (
      Math.abs(e.clientX - downX) > DRAG_THRESHOLD_PX ||
      Math.abs(e.clientY - downY) > DRAG_THRESHOLD_PX
    ) {
      return
    }
    onClickAt(canvas, e)
  }

  canvas.addEventListener('mousedown', onDown)
  canvas.addEventListener('mouseup', onUp)
  return () => {
    canvas.removeEventListener('mousedown', onDown)
    canvas.removeEventListener('mouseup', onUp)
  }
}

function ensureWired() {
  if (unsubscribe) return
  unsubscribe = subscribeViewport((handle) => {
    detach?.()
    detach = null
    camera = handle?.camera ?? null
    const canvas = handle?.controls.domElement as HTMLElement | undefined
    if (canvas) detach = attach(canvas)
  })
}

/** Register a patch viewer; returns the un-register. */
export function registerPatchPickSource(source: PatchPickSource): () => void {
  sources.add(source)
  ensureWired()
  return () => {
    sources.delete(source)
    if (sources.size === 0) {
      detach?.()
      detach = null
      unsubscribe?.()
      unsubscribe = null
      camera = null
    }
  }
}

/** Test seam: how many viewers are registered right now. */
export function patchPickSourceCount(): number {
  return sources.size
}
