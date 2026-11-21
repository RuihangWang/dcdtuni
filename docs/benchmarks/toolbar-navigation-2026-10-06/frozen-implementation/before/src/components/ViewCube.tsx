import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { animateViewPreset, cancelViewTransition, subscribeViewport, type ViewPreset, type ViewportHandle } from '../viewportBus'
import { orbitCameraByPixels } from '../interaction/viewCubeOrbit'
import { useTranslation } from '../useTranslation'

/**
 * The toolbar orientation cube.
 *
 * A second tiny renderer rather than an overlay in the main scene: the cube must
 * ignore the main camera's position and zoom and show only its ORIENTATION, and
 * it has to sit in the toolbar's DOM, not over the canvas.
 *
 * It is a two-gesture control, as in the legacy editor (`beginViewCubeDrag`,
 * STL_generate.html:17511): DRAG it to orbit the main camera, CLICK a face to
 * snap to that face's normal. A cube that only snaps is half a control — the
 * cube is the one place a user can turn the scene without putting the cursor
 * over the geometry they are trying to look at.
 */

/**
 * 56 px, a DELIBERATE divergence from the legacy's 88.
 *
 * The cube sits in a toolbar whose buttons are 24 px tall, and at 88 it is
 * nearly four rows high — it reads as the toolbar's subject rather than as one
 * of its controls. Set inline so it beats `#view-cube` in the stylesheet
 * without the two disagreeing about which owns the size.
 */
const CUBE_SIZE = 56

/** The legacy's `VIEWPORT_CLICK_DRAG_THRESHOLD` (STL_generate.html:2432). */
const DRAG_THRESHOLD = 5

const FACES: { preset: ViewPreset; label: string; color: number }[] = [
  // BoxGeometry material order: +X, -X, +Y, -Y, +Z, -Z
  { preset: 'right', label: 'R', color: 0xff5c72 },
  { preset: 'left', label: 'L', color: 0xb04050 },
  { preset: 'top', label: 'T', color: 0x00ff88 },
  { preset: 'bottom', label: 'B', color: 0x0a8a4c },
  { preset: 'front', label: 'F', color: 0x00d4ff },
  { preset: 'back', label: 'K', color: 0x0a7fa0 },
]

function faceTexture(label: string, color: number, pressed = false): THREE.Texture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.fillStyle = `#${color.toString(16).padStart(6, '0')}`
    ctx.fillRect(0, 0, size, size)
    if (pressed) {
      ctx.fillStyle = 'rgba(255,255,255,0.3)'
      ctx.fillRect(0, 0, size, size)
      ctx.shadowColor = '#ffffff'
      ctx.shadowBlur = 22
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 12
      ctx.strokeRect(7, 7, size - 14, size - 14)
      ctx.shadowBlur = 0
    } else {
      ctx.strokeStyle = 'rgba(0,0,0,0.55)'
      ctx.lineWidth = 8
      ctx.strokeRect(0, 0, size, size)
    }
    ctx.fillStyle = '#0d0d14'
    ctx.font = 'bold 64px monospace'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(label, size / 2, size / 2)
  }
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

export function ViewCube() {
  const { t } = useTranslation()
  const hostRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(CUBE_SIZE, CUBE_SIZE, false)
    host.appendChild(renderer.domElement)
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    // The canvas is a picture, not a target: every pointer event belongs to the
    // host div, which is what holds the drag and its pointer capture. The
    // legacy sets the same rule in CSS (`#view-cube canvas`, theme/base.css:78).
    renderer.domElement.style.pointerEvents = 'none'
    host.style.cursor = 'grab'

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 20)
    camera.position.set(0, 0, 4.2)

    scene.add(new THREE.AmbientLight(0xffffff, 2.4))
    const dir = new THREE.DirectionalLight(0xffffff, 1.6)
    dir.position.set(2, 3, 4)
    scene.add(dir)

    const normalMaps = FACES.map(f => faceTexture(f.label, f.color))
    const pressedMaps = FACES.map(f => faceTexture(f.label, f.color, true))
    const materials = normalMaps.map(map => new THREE.MeshBasicMaterial({ map }))
    const cube = new THREE.Mesh(new THREE.BoxGeometry(1.6, 1.6, 1.6), materials)
    scene.add(cube)

    // The cube shows the main camera's orientation: rotate it by the INVERSE of
    // that camera's rotation, so looking down the +Z axis shows the F face.
    let viewport: ViewportHandle | null = null
    const unsubscribe = subscribeViewport((h) => {
      viewport = h
    })

    let raf = 0
    const q = new THREE.Quaternion()
    const render = () => {
      raf = requestAnimationFrame(render)
      if (viewport) {
        q.copy(viewport.camera.quaternion).invert()
        cube.quaternion.copy(q)
      }
      renderer.render(scene, camera)
    }
    render()

    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()

    /** BoxGeometry face/material index under the cursor, or null for a miss. */
    const faceAt = (e: PointerEvent): number | null => {
      const rect = host.getBoundingClientRect()
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)
      const hit = raycaster.intersectObject(cube)[0]
      // materialIndex indexes BoxGeometry's face groups, which is exactly the
      // FACES order above.
      const idx = hit?.face?.materialIndex
      return typeof idx === 'number' && FACES[idx] ? idx : null
    }
    const presetAt = (e: PointerEvent): ViewPreset | null => {
      const index = faceAt(e)
      return index === null ? null : FACES[index]!.preset
    }
    let activeFace = -1
    let flashTimer: ReturnType<typeof setTimeout> | null = null
    const showFace = (index: number) => {
      if (activeFace >= 0) {
        materials[activeFace]!.map = normalMaps[activeFace]!
        materials[activeFace]!.needsUpdate = true
      }
      activeFace = index
      if (index >= 0) {
        materials[index]!.map = pressedMaps[index]!
        materials[index]!.needsUpdate = true
        host.dataset.activeFace = FACES[index]!.preset
      } else delete host.dataset.activeFace
    }
    const flashFace = (index: number) => {
      if (flashTimer) clearTimeout(flashTimer)
      showFace(index)
      flashTimer = setTimeout(() => { showFace(-1); flashTimer = null }, 550)
    }

    let drag: {
      pointerId: number
      startX: number
      startY: number
      lastX: number
      lastY: number
      moved: boolean
      /**
       * The face grabbed on the way DOWN.
       *
       * A click that ends a pixel outside the cube still means the face it
       * started on — the legacy keeps the same fallback (`fallbackAxis`,
       * STL_generate.html:17554) — otherwise a snap silently does nothing.
       */
      preset: ViewPreset | null
    } | null = null

    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return
      cancelViewTransition()
      drag = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        lastX: e.clientX,
        lastY: e.clientY,
        moved: false,
        preset: presetAt(e),
      }
      // The main OrbitControls is not involved in this gesture, but a drag that
      // wanders off the cube and over the canvas would otherwise start orbiting
      // there as well, and the camera would turn twice per pixel.
      if (viewport) viewport.controls.enabled = false
      host.style.cursor = 'grabbing'
      e.preventDefault()
      try {
        host.setPointerCapture(e.pointerId)
      } catch {
        // Capture is a convenience: without it the drag ends when the pointer
        // leaves the 56 px cube, which is a worse gesture but still a working one.
      }
    }

    const onMove = (e: PointerEvent) => {
      if (!drag || drag.pointerId !== e.pointerId) return
      e.preventDefault()
      const dx = e.clientX - drag.lastX
      const dy = e.clientY - drag.lastY
      if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) >= DRAG_THRESHOLD) {
        drag.moved = true
      }
      if (drag.moved && viewport) {
        // Geared to the MAIN canvas's height, not the cube's: the gesture is
        // meant to feel like dragging the scene, and 56 px would turn the
        // camera about sixteen times faster than the same drag in the viewport.
        const height = viewport.controls.domElement?.clientHeight ?? window.innerHeight
        orbitCameraByPixels(viewport.camera, viewport.controls, dx, dy, height)
      }
      drag.lastX = e.clientX
      drag.lastY = e.clientY
    }

    const onUp = (e: PointerEvent) => {
      if (!drag || drag.pointerId !== e.pointerId) return
      e.preventDefault()
      const { moved, preset } = drag
      drag = null
      try {
        host.releasePointerCapture(e.pointerId)
      } catch {
        // Already released — the pointer left the window mid-drag.
      }
      host.style.cursor = 'grab'
      if (viewport) viewport.controls.enabled = true
      // A drag is not also a click: snapping at the end of one would throw away
      // the orientation the user just dragged to.
      if (!moved) {
        const index = faceAt(e) ?? FACES.findIndex(face => face.preset === preset)
        if (index >= 0) {
          flashFace(index)
          animateViewPreset(FACES[index]!.preset)
        }
      }
    }

    const onCancel = (e: PointerEvent) => {
      if (!drag || drag.pointerId !== e.pointerId) return
      drag = null
      host.style.cursor = 'grab'
      if (viewport) viewport.controls.enabled = true
    }

    host.addEventListener('pointerdown', onDown)
    host.addEventListener('pointermove', onMove)
    host.addEventListener('pointerup', onUp)
    host.addEventListener('pointercancel', onCancel)

    return () => {
      cancelAnimationFrame(raf)
      cancelViewTransition()
      if (flashTimer) clearTimeout(flashTimer)
      showFace(-1)
      unsubscribe()
      host.removeEventListener('pointerdown', onDown)
      host.removeEventListener('pointermove', onMove)
      host.removeEventListener('pointerup', onUp)
      host.removeEventListener('pointercancel', onCancel)
      // A teardown mid-drag would otherwise leave the viewport frozen with no
      // cube left to release it.
      if (drag && viewport) viewport.controls.enabled = true
      materials.forEach(m => m.dispose())
      normalMaps.forEach(map => map.dispose())
      pressedMaps.forEach(map => map.dispose())
      cube.geometry.dispose()
      renderer.dispose()
      host.removeChild(renderer.domElement)
    }
  }, [])

  return (
    <div
      id="view-cube"
      ref={hostRef}
      title={t('tooltip.view_cube')}
      style={{ width: CUBE_SIZE, height: CUBE_SIZE, flex: `0 0 ${CUBE_SIZE}px`, touchAction: 'none' }}
    />
  )
}
