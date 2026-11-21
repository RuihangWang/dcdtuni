/**
 * Clicking the 3D view on the liquid post-processing tab selects what was hit.
 *
 * The geometry click is gated to the geometry tab (`canSelectGeometryFromViewport`):
 * there a click edits, here it only says "show me this". So the liquid results
 * panel registers this listener while it is mounted, the way the patch lists
 * register theirs (`patchViewportPick.ts`), and turns the hit into the same
 * selection the tree would make — a pipe, a valve or T on a pipe, a rack, a
 * server in a rack, a CDU. The panel then lights the rows that belong to it.
 *
 * A click is a press and release within 3 px, so an orbit that ends over a pipe
 * does not select it.
 */

import * as THREE from 'three'
import { subscribeViewport } from '../viewportBus'
import { focusEdgeIdsFor, type NetworkSelection } from './liquidNetwork'

const DRAG_THRESHOLD_PX = 3

type UserData = Record<string, unknown>

const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0

/**
 * The selection a hit means, from the hit object's userData and its parents',
 * nearest first. Null when nothing on the chain names a model.
 */
export function liquidPickSelection(chain: readonly UserData[]): NetworkSelection | null {
  for (const d of chain) {
    if (str(d.valveId) && str(d.pipeId)) {
      return { uid: d.pipeId, node: { type: 'fitting', uid: d.pipeId, fittingId: d.valveId } }
    }
    if (str(d.junctionId)) return { uid: d.junctionId, node: null }
    if (str(d.pipeModelId)) return { uid: d.pipeModelId, node: null }
    if (d.isBlindEndCap && str(d.pipeId)) return { uid: d.pipeId, node: null }
    if (d.isRackEquipment && str(d.equipmentId) && str(d.modelId)) {
      return { uid: d.modelId, node: { type: 'equipment', uid: d.modelId, equipmentId: d.equipmentId } }
    }
    if (str(d.modelId)) return { uid: d.modelId, node: null }
    if (str(d.uid)) return { uid: d.uid, node: null }
  }
  return null
}

/** Whether a selection owns at least one edge of the result `raw` (through object_bindings). */
export function resultKnows(raw: unknown): (selection: NetworkSelection) => boolean {
  return (selection) => focusEdgeIdsFor(raw, [selection.uid], selection.node).length > 0
}

/**
 * What a click means, from the userData chains of its hits, nearest hit first:
 * the nearest hit `prefer` accepts, else the nearest that names a model.
 *
 * On the results tab the objects of interest are the result's (pipes, valves,
 * servers, CDUs); a translucent raised floor or a wall in front of them would
 * otherwise take every click (seen in the browser check: clicks on the supply
 * ring under Shinaide's raised floor selected the floor).
 */
export function pickFromHits(
  chains: readonly (readonly UserData[])[],
  prefer?: (selection: NetworkSelection) => boolean,
): NetworkSelection | null {
  let nearest: NetworkSelection | null = null
  for (const chain of chains) {
    const selection = liquidPickSelection(chain)
    if (!selection) continue
    if (!prefer || prefer(selection)) return selection
    nearest ??= selection
  }
  return nearest
}

function shown(o: THREE.Object3D): boolean {
  for (let p: THREE.Object3D | null = o; p; p = p.parent) if (!p.visible) return false
  return true
}

function chainOf(o: THREE.Object3D): UserData[] {
  const out: UserData[] = []
  for (let p: THREE.Object3D | null = o; p; p = p.parent) out.push(p.userData as UserData)
  return out
}

/** The scene root the builders draw into (the legacy global the colouring also uses). */
function sceneRoot(): THREE.Object3D | null {
  return ((window as unknown as { globalGroup?: THREE.Object3D }).globalGroup) ?? null
}

/**
 * Listen for clicks on the 3D view and report what they select. Returns the
 * detach function.
 */
export function attachLiquidResultPicker(
  onPick: (selection: NetworkSelection) => void,
  prefer?: (selection: NetworkSelection) => boolean,
): () => void {
  const raycaster = new THREE.Raycaster()
  let camera: THREE.Camera | null = null
  let detachCanvas: (() => void) | null = null

  const attach = (canvas: HTMLElement) => {
    let downX = 0, downY = 0, down = false
    const onDown = (e: MouseEvent) => { downX = e.clientX; downY = e.clientY; down = true }
    const onUp = (e: MouseEvent) => {
      if (!down) return
      down = false
      if (Math.abs(e.clientX - downX) > DRAG_THRESHOLD_PX || Math.abs(e.clientY - downY) > DRAG_THRESHOLD_PX) return
      const root = sceneRoot()
      if (!root || !camera) return
      const rect = canvas.getBoundingClientRect()
      raycaster.setFromCamera(new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1,
      ), camera)
      const chains = raycaster.intersectObject(root, true)
        .map((hit) => hit.object as THREE.Mesh)
        .filter((o) => o.isMesh && shown(o) && o.userData.selectable !== false)
        .map(chainOf)
      const selection = pickFromHits(chains, prefer)
      if (selection) onPick(selection)
    }
    canvas.addEventListener('mousedown', onDown)
    canvas.addEventListener('mouseup', onUp)
    return () => {
      canvas.removeEventListener('mousedown', onDown)
      canvas.removeEventListener('mouseup', onUp)
    }
  }

  const unsubscribe = subscribeViewport((handle) => {
    detachCanvas?.()
    detachCanvas = null
    camera = handle?.camera ?? null
    const canvas = handle?.controls.domElement as HTMLElement | undefined
    if (canvas) detachCanvas = attach(canvas)
  })
  return () => {
    unsubscribe()
    detachCanvas?.()
    detachCanvas = null
  }
}
