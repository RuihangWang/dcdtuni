/**
 * Grabbing the post-processing handles in the 3D view.
 *
 * Two controls, one file: the slice preview ring slides along its own normal,
 * and the AI inference box moves by its centre sphere or resizes by one of its
 * six face planes. Both are ports of listeners the legacy editor installs on
 * the canvas — the ring at STL_generate.html:22768, the box at :20696 and
 * :20744 — and both exist for the same reason: a region you can only type six
 * numbers into is a region you cannot place over the geometry you meant.
 *
 * ## Why this attaches from the panel instead of living in the viewport
 *
 * `Viewport.tsx` already publishes its Three.js handles on `window.__three`,
 * the seam the legacy page uses. Reading them here keeps the post-processing
 * interaction with the post-processing code, and — more usefully — ties the
 * listeners' LIFETIME to the panel: the legacy handlers all begin with
 * `activeTab !== 'postprocess'` guards, and a panel that unmounts on a tab
 * switch expresses the same thing without a guard to forget.
 *
 * ## The drag mutates the scene, and only COMMITS on release
 *
 * Both handles move their Three.js objects directly while the pointer is down
 * and report the final value once. Pushing every frame through React would
 * re-render the panel and rebuild every post-processing layer — a 164568-point
 * boundary surface among them — sixty times a second to move one number, and
 * the drag would stutter to a halt on any real result. The seed sphere's gizmo
 * already works this way (`setPpSeedDrag`, reported on drag END).
 */

import * as THREE from 'three'
import {
  AI_BOX_FACES,
  aiBoxFaceCursor,
  moveAiBox,
  resizeAiBox,
  slideAlongNormal,
  type AiBoxBounds,
  type Vec3,
} from './ppDragMath'
import {
  SLICE_RING_COLOR,
  SLICE_RING_HOVER_COLOR,
  updateAiBoxVisuals,
  type AiBoxHover,
} from './ppRenderer'

interface ViewportHandles {
  camera: THREE.Camera
  renderer: { domElement: HTMLCanvasElement }
  controls: { enabled: boolean }
  scene: THREE.Object3D
}

function handles(): ViewportHandles | null {
  const three = (window as unknown as { __three?: Partial<ViewportHandles> }).__three
  if (!three?.camera || !three.renderer?.domElement || !three.scene || !three.controls) return null
  return three as ViewportHandles
}

/** The first object in the scene tagged with this post-processing layer. */
function findLayer(scene: THREE.Object3D, layer: string): THREE.Object3D | null {
  const found: THREE.Object3D[] = []
  scene.traverse((o) => {
    if (found.length) return
    if ((o.userData as { ppLayer?: string }).ppLayer === layer) found.push(o)
  })
  return found[0] ?? null
}

function setPointer(pointer: THREE.Vector2, canvas: HTMLElement, event: PointerEvent) {
  const rect = canvas.getBoundingClientRect()
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
}

/**
 * Swallow the click that ends a drag.
 *
 * The viewport picks the selection on `click` with a 4 px slop, and a grab that
 * ends within four pixels of where it started — a small nudge of the ring — was
 * therefore also a click on empty space, which clears the selection. Capturing
 * on `window` runs before the canvas's own listener, so the click never reaches
 * it. Registered only after a real drag, so an ordinary click still selects.
 */
function swallowNextClick() {
  const stop = (e: MouseEvent) => {
    e.stopPropagation()
  }
  window.addEventListener('click', stop, { capture: true, once: true })
  // A drag that somehow produces no click would leave the listener armed for
  // the NEXT one, which would eat a legitimate selection.
  setTimeout(() => window.removeEventListener('click', stop, { capture: true }), 300)
}

/** True when the event happened over the viewport canvas rather than a panel. */
function onCanvas(event: PointerEvent): boolean {
  const h = handles()
  return !!h && event.target === h.renderer.domElement
}

/**
 * Wire one handle's four listeners up.
 *
 * On `window`, not on the canvas: the panel can mount before `window.__three`
 * exists, and a canvas resolved at attach time would then be `undefined` and
 * the handle silently dead for the rest of the session. `pointerdown` is
 * CAPTURING so `controls.enabled = false` lands before OrbitControls' own
 * canvas listener sees the same event and starts orbiting under the grab.
 */
function listen(
  onHover: (e: PointerEvent) => void,
  onDown: (e: PointerEvent) => void,
  onMove: (e: PointerEvent) => void,
  onUp: () => void,
  release: () => void,
): () => void {
  const hover = (e: PointerEvent) => {
    if (onCanvas(e)) onHover(e)
  }
  const down = (e: PointerEvent) => {
    if (onCanvas(e)) onDown(e)
  }
  window.addEventListener('pointermove', hover)
  window.addEventListener('pointerdown', down, { capture: true })
  window.addEventListener('pointermove', onMove)
  window.addEventListener('pointerup', onUp)
  return () => {
    window.removeEventListener('pointermove', hover)
    window.removeEventListener('pointerdown', down, { capture: true })
    window.removeEventListener('pointermove', onMove)
    window.removeEventListener('pointerup', onUp)
    release()
  }
}

export interface SliceRingDragOptions {
  /** The plane's normal, read when the ring is grabbed. */
  normal(): Vec3
  /** The ring's final world position, once the pointer is released. */
  onCommit(origin: Vec3): void
}

/**
 * Hover-highlight and drag the slice preview ring.
 *
 * The ring slides along the plane NORMAL and nowhere else: a cut plane has one
 * degree of freedom, and letting the ring follow the cursor freely would move
 * the plane sideways within itself, which changes the origin numbers and cuts
 * exactly the same triangles.
 */
export function attachSliceRingDrag(opts: SliceRingDragOptions): () => void {
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  const dragPlane = new THREE.Plane()
  const startPosition = new THREE.Vector3()
  const startHit = new THREE.Vector3()
  const hit = new THREE.Vector3()
  const cameraDirection = new THREE.Vector3()
  let hovered = false
  let dragging = false
  let normal: Vec3 = [0, 1, 0]
  let cached: THREE.Mesh | null = null

  /**
   * The ring object, re-found when the layers are rebuilt.
   *
   * `applyPostprocessLayers` disposes and re-creates every layer on each apply,
   * so a reference held across one points at a freed object whose material
   * still recolours and whose position still moves — invisibly, because it is
   * no longer in the scene. Losing its parent is the signal that happened.
   */
  function ring(scene: THREE.Object3D): THREE.Mesh | null {
    if (cached && cached.parent) return cached
    const next = findLayer(scene, 'sliceRing') as THREE.Mesh | null
    // The hover COLOUR lives on the object, so a rebuilt ring starts at the
    // resting one while the flag still says hovered — and the guard that skips
    // a repaint when nothing changed would leave it that way until the cursor
    // left the ring and came back. Forgetting the hover is what re-paints it.
    if (next !== cached) hovered = false
    cached = next
    return cached
  }

  function paint(object: THREE.Mesh, isHovered: boolean) {
    const material = object.material as THREE.MeshBasicMaterial
    material.color.setHex(isHovered ? SLICE_RING_HOVER_COLOR : SLICE_RING_COLOR)
    material.opacity = isHovered ? 1.0 : 0.8
  }

  const onHover = (event: PointerEvent) => {
    if (dragging) return
    const h = handles()
    if (!h) return
    const object = ring(h.scene)
    if (!object) {
      if (hovered) {
        hovered = false
        h.renderer.domElement.style.cursor = ''
      }
      return
    }
    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    const hits = raycaster.intersectObject(object, false)
    if (hits.length > 0) {
      if (!hovered) {
        hovered = true
        paint(object, true)
        h.renderer.domElement.style.cursor = 'grab'
      }
    } else if (hovered) {
      hovered = false
      paint(object, false)
      h.renderer.domElement.style.cursor = ''
    }
  }

  const onDown = (event: PointerEvent) => {
    if (!hovered || event.button !== 0) return
    const h = handles()
    if (!h) return
    const object = ring(h.scene)
    if (!object) return

    dragging = true
    // OrbitControls has already seen this pointerdown and begun a rotation; its
    // move handler returns early while `enabled` is false, which is how the
    // legacy stops the camera spinning under a grabbed handle too.
    h.controls.enabled = false
    h.renderer.domElement.style.cursor = 'grabbing'
    normal = opts.normal()
    startPosition.copy(object.position)
    // A plane facing the CAMERA, not the cut plane: the cursor has to be able
    // to pull the ring while looking straight down its normal, and a drag plane
    // containing that normal is edge-on exactly then.
    h.camera.getWorldDirection(cameraDirection)
    dragPlane.setFromNormalAndCoplanarPoint(cameraDirection, object.position)
    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    raycaster.ray.intersectPlane(dragPlane, startHit)
    event.preventDefault()
  }

  const onMove = (event: PointerEvent) => {
    if (!dragging) return
    const h = handles()
    if (!h) return
    const object = ring(h.scene)
    if (!object) {
      dragging = false
      return
    }
    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    if (!raycaster.ray.intersectPlane(dragPlane, hit)) return
    const next = slideAlongNormal(
      [startPosition.x, startPosition.y, startPosition.z],
      normal,
      [hit.x - startHit.x, hit.y - startHit.y, hit.z - startHit.z],
    )
    object.position.set(next[0], next[1], next[2])
  }

  const onUp = () => {
    if (!dragging) return
    dragging = false
    const h = handles()
    if (h) {
      h.controls.enabled = true
      h.renderer.domElement.style.cursor = hovered ? 'grab' : ''
      const object = ring(h.scene)
      if (object) opts.onCommit([object.position.x, object.position.y, object.position.z])
    }
    swallowNextClick()
  }

  return listen(onHover, onDown, onMove, onUp, () => {
    if (dragging) {
      const h = handles()
      if (h) h.controls.enabled = true
    }
  })
}

export interface AiBoxDragOptions {
  /** The box as the panel holds it, or null when none is placed. */
  bounds(): AiBoxBounds | null
  /** The box after a drag, once the pointer is released. */
  onCommit(box: AiBoxBounds): void
}

interface AiDragState {
  type: 'centre' | 'face'
  faceIdx: number
  start: AiBoxBounds
}

/**
 * Move and resize the AI inference box.
 *
 * The centre sphere translates the whole box; a face plane moves that one face
 * along its axis. The face's drag plane CONTAINS the axis and is turned to face
 * the camera around it, which is what stops a face from jumping when the view
 * is nearly edge-on to it — the legacy builds the same plane out of two cross
 * products (`beginPPAIBoxDrag`, STL_generate.html:20775).
 */
export function attachAiBoxDrag(opts: AiBoxDragOptions): () => void {
  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  const dragPlane = new THREE.Plane()
  const startHit = new THREE.Vector3()
  const hit = new THREE.Vector3()
  const cameraDirection = new THREE.Vector3()
  let hover: AiBoxHover = 'none'
  let hoverFace = -1
  let drag: AiDragState | null = null
  let live: AiBoxBounds | null = null
  let cached: THREE.Object3D | null = null

  function group(scene: THREE.Object3D): THREE.Object3D | null {
    if (cached && cached.parent) return cached
    const next = findLayer(scene, 'aiBox')
    // A rebuilt box already carries the committed bounds, so the drag's copy
    // stops being newer than the panel's and must not keep overriding it. Its
    // hover colour is gone with the old materials too, so the flag has to
    // forget as well or the next pointermove skips the repaint.
    if (next !== cached) {
      live = null
      hover = 'none'
      hoverFace = -1
    }
    cached = next
    return cached
  }

  function parts(root: THREE.Object3D) {
    const faces: THREE.Object3D[] = []
    let centre: THREE.Object3D | null = null
    for (const child of root.children) {
      const data = child.userData as { ppAiPart?: string; ppAiFace?: number }
      if (data.ppAiPart === 'centre') centre = child
      else if (typeof data.ppAiFace === 'number') faces.push(child)
    }
    return { faces, centre }
  }

  function repaint(root: THREE.Object3D, box: AiBoxBounds) {
    updateAiBoxVisuals(root, box.min, box.max, { hover, dragging: !!drag })
  }

  const onHover = (event: PointerEvent) => {
    if (drag) return
    const h = handles()
    if (!h) return
    const root = group(h.scene)
    // `live` is the drag's own copy, which is one React render ahead of the
    // panel's between a commit and the rebuild it triggers.
    const box = live ?? opts.bounds()
    if (!root || !box) {
      if (hover !== 'none') {
        hover = 'none'
        hoverFace = -1
        h.renderer.domElement.style.cursor = ''
      }
      return
    }
    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    const { faces, centre } = parts(root)

    // The centre sphere wins over the faces it sits between: it is the smaller
    // target and it is the one a user aims at deliberately.
    if (centre && raycaster.intersectObject(centre, false).length > 0) {
      if (hover !== 'centre') {
        hover = 'centre'
        hoverFace = -1
        h.renderer.domElement.style.cursor = 'grab'
        repaint(root, box)
      }
      return
    }
    const faceHits = raycaster.intersectObjects(faces, false)
    if (faceHits.length > 0) {
      const idx = (faceHits[0]!.object.userData as { ppAiFace?: number }).ppAiFace ?? -1
      if (hover !== 'face' || hoverFace !== idx) {
        hover = 'face'
        hoverFace = idx
        h.renderer.domElement.style.cursor = aiBoxFaceCursor(idx)
        repaint(root, box)
      }
      return
    }
    if (hover !== 'none') {
      hover = 'none'
      hoverFace = -1
      h.renderer.domElement.style.cursor = ''
      repaint(root, box)
    }
  }

  const onDown = (event: PointerEvent) => {
    if (hover === 'none' || event.button !== 0) return
    const h = handles()
    if (!h) return
    const root = group(h.scene)
    const box = live ?? opts.bounds()
    if (!root || !box) return

    const isCentre = hover === 'centre'
    const faceIdx = isCentre ? 0 : hoverFace
    const face = AI_BOX_FACES[faceIdx]
    if (!face) return

    const centre = new THREE.Vector3(
      (box.min[0] + box.max[0]) / 2,
      (box.min[1] + box.max[1]) / 2,
      (box.min[2] + box.max[2]) / 2,
    )
    h.camera.getWorldDirection(cameraDirection)
    if (isCentre) {
      dragPlane.setFromNormalAndCoplanarPoint(cameraDirection, centre)
    } else {
      const axis = new THREE.Vector3(0, 0, 0)
      axis.setComponent(face.axis, 1)
      const cross = new THREE.Vector3().crossVectors(axis, cameraDirection)
      const planeNormal = new THREE.Vector3().crossVectors(cross, axis).normalize()
      // Looking straight down the axis leaves the two crosses degenerate; the
      // camera plane is the only one left that the ray can still hit.
      if (planeNormal.lengthSq() < 1e-6) planeNormal.copy(cameraDirection)
      const faceCentre = centre.clone()
      faceCentre.setComponent(
        face.axis,
        (face.isMin ? box.min[face.axis] : box.max[face.axis]) as number,
      )
      dragPlane.setFromNormalAndCoplanarPoint(planeNormal, faceCentre)
    }

    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    if (!raycaster.ray.intersectPlane(dragPlane, startHit)) return

    drag = { type: isCentre ? 'centre' : 'face', faceIdx, start: box }
    live = box
    h.controls.enabled = false
    repaint(root, box)
    event.preventDefault()
  }

  const onMove = (event: PointerEvent) => {
    if (!drag) return
    const h = handles()
    if (!h) return
    const root = group(h.scene)
    if (!root) {
      drag = null
      return
    }
    setPointer(pointer, h.renderer.domElement, event)
    raycaster.setFromCamera(pointer, h.camera)
    if (!raycaster.ray.intersectPlane(dragPlane, hit)) return
    const delta: Vec3 = [hit.x - startHit.x, hit.y - startHit.y, hit.z - startHit.z]
    live =
      drag.type === 'centre'
        ? moveAiBox(drag.start, delta)
        : resizeAiBox(drag.start, drag.faceIdx, delta)
    repaint(root, live)
  }

  const onUp = () => {
    if (!drag) return
    const box = live
    drag = null
    const h = handles()
    if (h) {
      h.controls.enabled = true
      const root = group(h.scene)
      if (root && box) repaint(root, box)
    }
    if (box) opts.onCommit(box)
    swallowNextClick()
  }

  return listen(onHover, onDown, onMove, onUp, () => {
    if (drag) {
      const h = handles()
      if (h) h.controls.enabled = true
    }
  })
}
