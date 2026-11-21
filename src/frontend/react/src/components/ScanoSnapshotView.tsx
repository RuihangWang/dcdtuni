import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { getViewportHandle } from '../viewportBus'
import { buildSlice, buildSurface, fieldRange } from '../postprocess/ppRenderer'
import { extremePoints, volumeExtremes, type ExtremePair } from '../postprocess/ppExtremes'
import { buildVolume, type BuiltVolume } from '../postprocess/ppVolume'
import { filterIndices } from '../postprocess/ppRenderer'
import {
  getSnapshots, isSnapshotViewOpen, removeSnapshot, setSnapshotViewOpen, subscribeSnapshots, type ScanoSnapshot,
} from '../postprocess/scanoSnapshots'
import { isTemperatureField, scalarFieldUnit, tempUnitLabel, toDisplay } from '../postprocess/ppUnits'
import { useTranslation } from '../useTranslation'

/** Snapshot comparison: one pane per snapshot, one shared camera and one shared colour range per layer (surface,
 * each slice, the volume). */

/** The extremes a pane reports: the volume's when it has one, else the surface's. */
function paneExtremes(s: ScanoSnapshot): { pair: ExtremePair | null; volume: boolean } {
  if (s.volume) return { pair: volumeExtremes(s.volume.payload), volume: true }
  return { pair: extremePoints(s.mesh.positions, s.values, s.hidden.size ? filterIndices(s.mesh, s.hidden) : s.mesh.indices),
    volume: false }
}

function sharedRange(snaps: ScanoSnapshot[]): [number, number] {
  const vols = snaps.flatMap((s) => (s.volume ? [s.volume.clim] : []))
  return vols.length ? union(vols) : union(snaps.map((s) => fieldRange(s.values)))
}

function fmt(field: string, v: number): string {
  const unit = isTemperatureField(field) ? tempUnitLabel() : scalarFieldUnit(field)
  return `${toDisplay(field, v).toFixed(2)}${unit ? ' ' + unit : ''}`
}

function union(ranges: [number, number][]): [number, number] {
  return [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))]
}

function grid(n: number): [number, number] {
  const cols = n <= 1 ? 1 : n <= 4 ? 2 : 3
  return [cols, Math.ceil(n / cols)]
}

/** The main scene's models, sharing its geometry and materials (so its opacity), as context in a pane. */
function contextModels(): THREE.Group | null {
  const main = (window as unknown as { __three?: { scene?: THREE.Scene } }).__three?.scene
  if (!main) return null
  main.updateMatrixWorld()
  const group = new THREE.Group()
  main.traverseVisible((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh || !(mesh.userData as { modelId?: string }).modelId) return
    const copy = new THREE.Mesh(mesh.geometry, mesh.material)
    copy.matrixAutoUpdate = false
    copy.matrix.copy(mesh.matrixWorld)
    copy.renderOrder = mesh.renderOrder
    group.add(copy)
  })
  return group
}

/** The region every pane shows: the snapshots' volumes, else their meshes. */
function snapshotBounds(snaps: ScanoSnapshot[]): THREE.Box3 {
  const box = new THREE.Box3()
  for (const s of snaps) {
    if (s.volume) {
      const { origin, spacing, dimensions } = s.volume.payload
      box.expandByPoint(new THREE.Vector3(...origin))
      box.expandByPoint(new THREE.Vector3(...origin.map((o, i) => o + spacing[i]! * (dimensions[i]! - 1)) as [number, number, number]))
    } else {
      const p = s.mesh.positions
      for (let i = 0; i < p.length; i += 3) box.expandByPoint(new THREE.Vector3(p[i], p[i + 1], p[i + 2]))
    }
  }
  return box
}

export function ScanoSnapshotView() {
  const { t } = useTranslation()
  const open = useSyncExternalStore(subscribeSnapshots, isSnapshotViewOpen)
  const snaps = useSyncExternalStore(subscribeSnapshots, getSnapshots)
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [showExtremes, setShowExtremes] = useState(true)
  const extremes = useMemo(() => snaps.map(paneExtremes), [snaps])
  // the markers' DOM nodes, placed by the draw loop: [ring, leader, label] per snapshot and end
  const markers = useRef(new Map<string, { ring: SVGCircleElement | null; line: SVGLineElement | null; tag: HTMLDivElement | null }>())
  const showRef = useRef(showExtremes)
  showRef.current = showExtremes
  useEffect(() => {
    if (!open) return
    const target = document.querySelector('.viewport')
    if (!target) return
    const update = () => {
      const r = target.getBoundingClientRect()
      const left = document.getElementById('left-panel')
      const right = document.getElementById('right-panel')
      const x0 = Math.max(r.left, left && !left.hidden ? left.getBoundingClientRect().right : r.left)
      const x1 = Math.min(r.right, right && !right.hidden ? right.getBoundingClientRect().left : r.right)
      const bar = document.getElementById('toolbar')
      const y0 = Math.max(r.top, bar ? bar.getBoundingClientRect().bottom + 4 : r.top)
      setRect(new DOMRect(x0, y0, Math.max(0, x1 - x0), Math.max(0, r.bottom - y0)))
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(target)
    window.addEventListener('resize', update)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [open])

  useEffect(() => {
    const host = hostRef.current
    if (!open || !host || !snaps.length || !rect) return
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setScissorTest(true)
    renderer.setClearColor(0x161a21, 1)
    renderer.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%'
    host.prepend(renderer.domElement)

    const main = getViewportHandle()
    const camera = main ? main.camera.clone() : new THREE.PerspectiveCamera(45, 1, 0.1, 1000)
    camera.layers.enableAll()                            // the main camera's layer mask is the viewport's, not ours
    const controls = new OrbitControls(camera, renderer.domElement)
    // the main view's direction, at the distance that fits the whole region into one pane
    const bounds = snapshotBounds(snaps)
    if (!bounds.isEmpty()) {
      const centre = bounds.getCenter(new THREE.Vector3())
      const radius = bounds.getBoundingSphere(new THREE.Sphere()).radius
      const dir = main ? main.camera.position.clone().sub(main.controls.target).normalize() : new THREE.Vector3(1, 1, 1).normalize()
      const [cols, rows] = grid(snaps.length)
      const aspect = (rect!.width / cols) / Math.max(1, rect!.height / rows)
      const vfov = THREE.MathUtils.degToRad(camera.fov)
      const hfov = 2 * Math.atan(Math.tan(vfov / 2) * aspect)
      const dist = (radius / Math.sin(Math.min(vfov, hfov) / 2)) * 0.92
      controls.target.copy(centre)
      camera.position.copy(centre).addScaledVector(dir, dist)
      camera.near = Math.max(0.01, dist / 200)
      camera.far = dist + radius * 4
    } else if (main) {
      controls.target.copy(main.controls.target)
    }
    controls.update()

    const surfaceRange = union(snaps.map((s) => fieldRange(s.values)))
    const sliceRanges = new Map<string, [number, number]>()
    for (const s of snaps) {
      for (const c of s.slices) {
        const r = c.payload.scalarRange
        const old = sliceRanges.get(c.label)
        sliceRanges.set(c.label, old ? union([old, r]) : r)
      }
    }
    const volumeClims = snaps.flatMap((s) => (s.volume ? [s.volume.clim] : []))
    const volumeRange: [number, number] = volumeClims.length ? union(volumeClims) : [0, 1]
    const built: { dispose(): void }[] = []
    const volumes: BuiltVolume[] = []
    const mainScene = (window as unknown as { __three?: { scene?: THREE.Scene } }).__three?.scene
    const scenes = snaps.map((s) => {
      const scene = new THREE.Scene()
      // the room for orientation, lit as the main view is (its environment map) plus a soft fill
      scene.environment = mainScene?.environment ?? null
      scene.add(new THREE.HemisphereLight(0xffffff, 0x404850, 1.2))
      const models = contextModels()
      if (models) scene.add(models)
      if (s.surfaceVisible !== false) {
        const surface = buildSurface(s.mesh, { values: s.values, climMin: surfaceRange[0], climMax: surfaceRange[1],
          autoScale: false, colorMap: s.colorMap, hidden: s.hidden })
        scene.add(surface.object)
        built.push(surface)
      }
      if (s.volume) {
        const v = buildVolume(s.volume.payload, s.volume.colorMap, volumeRange, s.volume.opacity, s.volume.resolution,
          s.volume.threshold)
        scene.add(v.object)
        built.push(v)
        volumes.push(v)
      }
      for (const c of s.slices) {
        const slice = buildSlice(c.payload, c.colorMap, sliceRanges.get(c.label))
        scene.add(slice.object)
        built.push(slice)
      }
      return scene
    })

    let frame = 0
    const draw = () => {
      frame = requestAnimationFrame(draw)
      const w = host.clientWidth
      const h = host.clientHeight
      const size = renderer.getSize(new THREE.Vector2())
      if (size.x !== w || size.y !== h) renderer.setSize(w, h, false)
      const [cols, rows] = grid(scenes.length)
      const pw = Math.floor(w / cols)
      const ph = Math.floor(h / rows)
      controls.update()
      for (const v of volumes) v.setCameraPosition(camera.position)   // the ray marches from the camera
      camera.aspect = pw / ph
      camera.updateProjectionMatrix()
      scenes.forEach((scene, i) => {
        const x = (i % cols) * pw
        const y = h - (Math.floor(i / cols) + 1) * ph           // WebGL viewports count from the bottom
        renderer.setViewport(x + 1, y + 1, pw - 2, ph - 2)
        renderer.setScissor(x + 1, y + 1, pw - 2, ph - 2)
        renderer.render(scene, camera)
      })
      // max / min markers follow the shared camera, each inside its own pane
      snaps.forEach((snap, i) => {
        const pair = extremes[i]?.pair
        const left = (i % cols) * pw
        const top = Math.floor(i / cols) * ph
        for (const end of ['max', 'min'] as const) {
          const node = markers.current.get(`${snap.id}:${end}`)
          if (!node?.ring || !node.line || !node.tag) continue
          const at = pair ? new THREE.Vector3(...pair[end].position).project(camera) : null
          const px = at ? left + ((at.x + 1) / 2) * pw : 0
          const py = at ? top + ((1 - at.y) / 2) * ph : 0
          const visible = !!at && showRef.current && at.z < 1 && px > left && px < left + pw && py > top && py < top + ph
          node.ring.style.display = node.line.style.display = node.tag.style.display = visible ? '' : 'none'
          if (!visible) continue
          // max above to the right, min below to the left: the two never cover each other
          const tx = px + (end === 'max' ? 26 : -26)
          const ty = py + (end === 'max' ? -26 : 24)
          node.ring.setAttribute('cx', String(px)); node.ring.setAttribute('cy', String(py))
          node.line.setAttribute('x1', String(px)); node.line.setAttribute('y1', String(py))
          node.line.setAttribute('x2', String(tx)); node.line.setAttribute('y2', String(ty))
          node.tag.style.transform = `translate(${tx}px, ${ty}px) translate(${end === 'max' ? '0' : '-100%'}, -50%)`
        }
      })
    }
    draw()
    return () => {
      cancelAnimationFrame(frame)
      controls.dispose()
      for (const b of built) b.dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
    }
  }, [open, snaps, rect !== null])

  if (!open || !snaps.length || !rect) return null
  const [cols, rows] = grid(snaps.length)
  const slot = (key: string) => {
    let n = markers.current.get(key)
    if (!n) markers.current.set(key, n = { ring: null, line: null, tag: null })
    return n
  }
  return createPortal(
    <div ref={hostRef} id="scano-snapshot-view"
      style={{ position: 'fixed', left: rect.left, top: rect.top, width: rect.width, height: rect.height, zIndex: 1000,
        background: '#161a21', overflow: 'hidden' }}>
      {snaps.map((s: ScanoSnapshot, i) => {
        const { pair, volume } = paneExtremes(s)
        return (
          <div key={s.id} className="scano-snapshot-pane" data-snapshot={s.id}
            style={{ position: 'absolute', left: `${((i % cols) * 100) / cols}%`, top: `${(Math.floor(i / cols) * 100) / rows}%`,
              width: `${100 / cols}%`, height: `${100 / rows}%`, pointerEvents: 'none', boxSizing: 'border-box',
              border: '1px solid #2c333f' }}>
            <div style={{ position: 'absolute', left: 8, top: 6, right: 8, fontSize: 12, lineHeight: 1.45, color: '#e6e6e6',
              textShadow: '0 1px 2px #000' }}>
              <div style={{ fontWeight: 600, color: '#d2a8ff' }}>
                {t('scano.snap.pane', { n: s.id, time: s.takenAt })}
                <button type="button" className="scano-snapshot-remove" style={{ pointerEvents: 'auto', marginLeft: 8 }}
                  onClick={() => removeSnapshot(s.id)}>✕</button>
              </div>
              {s.bc.map((line) => <div key={line}>{line}</div>)}
              {pair && (
                <div className="scano-snapshot-extremes">
                  <span style={{ color: '#ff8a8a' }}>▲ {volume ? t('scano.snap.volume') + ' ' : ''}{s.field} {fmt(s.field, pair.max.value)}</span>{'  '}
                  <span style={{ color: '#8ac8ff' }}>▼ {fmt(s.field, pair.min.value)}</span>
                </div>
              )}
            </div>
          </div>
        )
      })}
      <svg className="extreme-tag-lines" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
        pointerEvents: 'none' }}>
        {snaps.flatMap((sn) => (['max', 'min'] as const).map((end) => (
          <g key={`${sn.id}:${end}`} className={`extreme-tag-${end}`}>
            <line ref={(el) => { slot(`${sn.id}:${end}`).line = el }} />
            <circle ref={(el) => { slot(`${sn.id}:${end}`).ring = el }} r={6} />
          </g>
        )))}
      </svg>
      {snaps.flatMap((sn, i) => (['max', 'min'] as const).map((end) => {
        const e = extremes[i]
        const pair = e?.pair
        return pair ? (
          <div key={`${sn.id}:${end}`} ref={(el) => { slot(`${sn.id}:${end}`).tag = el }}
            className={`extreme-tag extreme-tag-${end} scano-snapshot-extreme`} data-snapshot-extreme={`${sn.id}:${end}`}
            style={{ position: 'absolute', pointerEvents: 'none', cursor: 'default', paddingRight: 8 }}>
            {(end === 'max' ? '▲ ' : '▼ ') + (e.volume ? t('scano.snap.volume') + ' ' : '') +
              t(end === 'max' ? 'scano.snap.max' : 'scano.snap.min') + ' ' + fmt(sn.field, pair[end].value)}
          </div>
        ) : null
      }))}
      <div style={{ position: 'absolute', right: 10, bottom: 10, display: 'flex', gap: 8, alignItems: 'center',
        color: '#ccc', fontSize: 12 }}>
        <span>{t('scano.snap.shared_range', {
          lo: fmt(snaps[0]!.field, sharedRange(snaps)[0]),
          hi: fmt(snaps[0]!.field, sharedRange(snaps)[1]) })}</span>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" id="scano-snapshot-extremes" checked={showExtremes}
            onChange={(e) => setShowExtremes(e.target.checked)} />
          {t('pp.show_extremes')}
        </label>
        <button type="button" id="scano-snapshot-close" onClick={() => setSnapshotViewOpen(false)}>{t('scano.snap.close')}</button>
      </div>
    </div>,
    document.body,
  )
}
