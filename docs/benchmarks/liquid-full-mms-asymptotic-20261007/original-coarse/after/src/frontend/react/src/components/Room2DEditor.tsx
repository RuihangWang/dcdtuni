import {
  createElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import type { CfdProject } from '../schema/cfdproj'
import { generateRoom, polygonArea, rectangleFootprint, signedArea2 } from '../room2d/room2d'
import * as store from '../projectStore'
import { fromDisplay, toDisplay, unitConfig, unitOf, type LengthUnit } from '../lengthUnits'
import { requestSelect } from '../interaction/selectionBridge'
import { useTranslation } from '../useTranslation'
import { inferRoomHeight } from '../run/meshPreflight'
import { NumberField } from './NumberField'
import {
  canvasToWorld,
  clampScale,
  edgeAngleDeg,
  fitView,
  perimeter,
  pointToSegmentDist,
  worldToCanvas,
  type Point,
  type Vertex2,
  type View,
} from '../room2d/room2dView'
import { hitModel, hitRotateRing, planModels, rotateRing, RING_HOVER_PX } from '../room2d/room2dModels'
import { computeAnnotations } from '../room2d/room2dAnnotations'
import {
  applySnap,
  boxSelect,
  breakEdge,
  deleteVertices,
  snapModelAnchor,
  type SnapModes,
} from '../room2d/room2dSnap'
import { CLOSE_PX, DEFAULT_BG, drawPlan, extentPoints, type PlanScene } from '../room2d/room2dDraw'
import {
  COORD_TOOLTIP_OFFSET,
  coordDisplay,
  coordPlacement,
  nextCoordField,
  parseCoordEntry,
} from '../room2d/coordTooltip'
import {
  commitPlacement,
  dragOrigins,
  placeLive,
  snapBoxes,
  type DragOrigin,
  type PlanPlacement,
} from '../room2d/room2dActions'
import { registerEditor, type Room2DHandle } from '../room2d/room2dRegistry'
import { dropStandaloneIfAny, registerMounter } from '../room2d/room2dApi'
import {
  claimRoom2dPanel,
  getRoom2dPanelHost,
  releaseRoom2dPanel,
  subscribeRoom2dPanel,
} from '../room2d/room2dPanelSlot'
import { isRotationSnapOn, setRotationSnap } from '../room2d/rotationSnapBridge'
import { rotateAnchor, rotateQuaternion, snapDelta } from '../interaction/rotateGizmo'

/**
 * The 2D room designer: draw a footprint, lay models out on it, get walls +
 * floor + ceiling.
 *
 * A port of `web/room_2d_editor.js`, not a wrapper around it. That file is a UI
 * CONTROLLER, not a format module: it reads a global `state`, `uiState` and
 * `THREE`, calls some thirty page globals (`setSelection`, `updateUI`,
 * `rebuildGeometry`, `getModelOBB`, …) and writes its side panel into
 * `#prop-content` as innerHTML with inline `onclick="room2d..."` handlers.
 * Reusing it would mean standing up a fake legacy app inside React and letting
 * it write into a DOM node React owns. The shared `web/js` modules this port
 * DOES reuse — `cfdproj_state_v4.js`, `transform_tools.js` — are the opposite:
 * pure, DOM-free, and the single definition of the file format.
 *
 * What is reused is everything that decides a NUMBER: `stateV4().allModels` for
 * model order, the `rotateGizmo` maths the 3D ring already uses, and the
 * `web/i18n` dictionaries. The canvas painting is ported line for line
 * (`room2dDraw.ts`) because this plan is the topview the 3-agent SOP
 * screenshots, and `window.room2d` answers the same calls the SOP makes.
 */

const HIT_PX = 10
const EDGE_HIT_PX = 8

export interface Room2DEditorProps {
  project: CfdProject
  onClose: () => void
  /**
   * True for the tree `window.room2d.enterEditor()` mounts on its own.
   *
   * It must NOT tear that tree down on registration — which is what an
   * App-mounted editor does, to stop an invisible API-mounted one from holding
   * the handle. Without the distinction the standalone editor unmounts itself
   * one tick after it appears.
   */
  standalone?: boolean
}

type Tool = 'draw' | 'select'

type Drag =
  | { kind: 'pan'; clientX: number; clientY: number; offsetX: number; offsetY: number }
  | { kind: 'vertex'; index: number }
  | { kind: 'edge'; index: number; last: Vertex2 }
  | { kind: 'model'; origins: DragOrigin[]; start: Vertex2 }
  | {
      kind: 'rotate'
      origins: DragOrigin[]
      pivots: Map<string, Point>
      startAngle: number
      grabAngle: number
      centre: Point
    }
  | { kind: 'band'; start: Point }

export function Room2DEditor({ project, onClose, standalone = false }: Room2DEditorProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)

  const initial = useMemo<Vertex2[]>(() => {
    const plane = project.geometry?.plane
    return plane && plane.length >= 3 ? plane.map((p) => [p[0], p[1]] as Vertex2) : []
  }, [project])

  const [vertices, setVertices] = useState<Vertex2[]>(initial)
  const [closed, setClosed] = useState(initial.length >= 3)
  // Derived from the CEILING that exists, not from the stored `planeHeight`.
  //
  // The two disagree the moment somebody moves the ceiling in 3D: `planeHeight`
  // is what the 2D designer last wrote, and nothing updates it when the ceiling
  // model itself is dragged. Opening the designer on a stale number and then
  // pressing 应用 drags the ceiling back down to it — a silent geometry change
  // nobody asked for. `inferRoomHeight` is the same rule the legacy runs first
  // thing in `room2d.enterEditor` (`_syncRoomPolygonHeightFromExplicitCeiling`,
  // room_2d_editor.js:137): the highest `roomBoundaryLocation: 'top'` floor
  // wins, and `planeHeight` is only the fallback when there is no such ceiling.
  const [height, setHeight] = useState<number>(() => inferRoomHeight(project) || 3.0)
  const [tool, setTool] = useState<Tool>(initial.length >= 3 ? 'select' : 'draw')
  const [snap, setSnap] = useState<SnapModes>({ grid: true, vertex: false, edge: false })
  const [rotSnap, setRotSnap] = useState(() => isRotationSnapOn())
  const [gridSpacing, setGridSpacing] = useState((project.geometry?.tileSize ?? 0.6) || 0.6)
  const [view, setView] = useState<View>({ scale: 40, offsetX: 0, offsetY: 0 })
  const [rotation, setRotation] = useState(0)
  const [showAxes, setShowAxes] = useState(true)
  const [showAnnotations, setShowAnnotations] = useState(false)
  const [bgColor, setBgColor] = useState(DEFAULT_BG)
  const [hover, setHover] = useState<Vertex2 | null>(null)
  const [selectedVertex, setSelectedVertex] = useState(-1)
  const [selectedEdge, setSelectedEdge] = useState(-1)
  const [boxSel, setBoxSel] = useState<{ vertices: number[]; edges: number[] }>({
    vertices: [],
    edges: [],
  })
  const [band, setBand] = useState<{ a: Point; b: Point } | null>(null)
  const [selectedUids, setSelectedUids] = useState<Set<string>>(new Set())
  const [rotatingUid, setRotatingUid] = useState<string | null>(null)
  const [rotateHoverUid, setRotateHoverUid] = useState<string | null>(null)
  const [rotateAngle, setRotateAngle] = useState<number | null>(null)
  const [shiftHeld, setShiftHeld] = useState(false)
  // Bumped on every frame of a model drag. The positions themselves live in the
  // project (mutated in place, no undo entry per frame), so nothing else would
  // tell React the canvas is stale.
  const [, setLiveTick] = useState(0)

  const dragRef = useRef<Drag | null>(null)
  const [dragging, setDragging] = useState(false)

  // The typed-coordinate cursor. `coordPreview` is a point Enter has moved the
  // cursor to but NOT placed; `coordFocused` freezes the boxes while one holds
  // focus, or the next mouse move would overwrite what is being typed.
  const [coordPreview, setCoordPreview] = useState<Vertex2 | null>(null)
  const [coordFocused, setCoordFocused] = useState(false)
  const coordXRef = useRef<HTMLInputElement | null>(null)
  const coordYRef = useRef<HTMLInputElement | null>(null)

  const unitId = unitOf(project)
  const unit = unitConfig(project)
  const formatLength = useCallback(
    (metres: number) => (metres / unit.scale).toFixed(unit.precision),
    [unit],
  )

  const models = planModels(store.getProject() ?? project)

  // --- view transform -----------------------------------------------------
  // Measured off the HOST, not the canvas: a canvas that has not been painted
  // yet still reports the element default of 300x150, and a zoom-to-fit against
  // that lands the room at a fifth of the right scale.
  const size = () => {
    const host = hostRef.current
    return { w: host?.clientWidth ?? 0, h: host?.clientHeight ?? 0 }
  }
  const toCanvas = useCallback(
    (wx: number, wy: number): Point => {
      const canvas = canvasRef.current
      return worldToCanvas(view, rotation, canvas?.width ?? 0, canvas?.height ?? 0, wx, wy)
    },
    [view, rotation],
  )
  const toWorld = useCallback(
    (cx: number, cy: number): Vertex2 => {
      const canvas = canvasRef.current
      const p = canvasToWorld(view, rotation, canvas?.width ?? 0, canvas?.height ?? 0, cx, cy)
      return [p.x, p.y]
    },
    [view, rotation],
  )

  const snapPoint = useCallback(
    (p: Vertex2, options: { dragVertex?: number; constrain?: boolean } = {}): Vertex2 =>
      applySnap(p[0], p[1], {
        modes: snap,
        gridSpacing,
        scale: view.scale,
        boxes: models.map((m) => ({
          uid: m.uid,
          x: m.location.x,
          y: m.location.z,
          w: m.size.x,
          d: m.size.z,
        })),
        vertices,
        dragVertex: options.dragVertex ?? -1,
        shiftHeld,
        constrainFrom:
          options.constrain && !closed && vertices.length > 0
            ? vertices[vertices.length - 1]!
            : null,
      }),
    [snap, gridSpacing, view.scale, models, vertices, shiftHeld, closed],
  )

  const zoomToFit = useCallback(() => {
    const { w, h } = size()
    const next = fitView(extentPoints(vertices, models), rotation, w, h)
    if (next) setView(next)
  }, [vertices, models, rotation])

  // Frame whatever the project already has, once the canvas has a size. The
  // legacy defers its own fit past `_resizeCanvas` for the same reason: a fit
  // against a zero-width canvas produces an infinite scale.
  const framedRef = useRef(false)
  useLayoutEffect(() => {
    if (framedRef.current) return
    const { w, h } = size()
    if (w === 0 || h === 0) return
    framedRef.current = true
    const next = fitView(extentPoints(initial, models), 0, w, h)
    if (next) setView(next)
  })

  // --- drawing ------------------------------------------------------------
  // The scene is mirrored into a ref every render so `paint()` can be called
  // from outside one — the PNG export and the ResizeObserver both need to draw
  // with no state change to hang an effect on.
  const sceneRef = useRef<PlanScene | null>(null)
  const scene: PlanScene = {
    width: 0,
    height: 0,
    view,
    rotation,
    bgColor,
    showAxes,
    showAnnotations,
    gridSpacing,
    vertices,
    closed,
    tool,
    selectedVertex,
    selectedEdge,
    boxSelectedVertices: boxSel.vertices,
    boxSelectedEdges: boxSel.edges,
    band,
    models,
    selectedUids,
    rotatingUid,
    rotateHoverUid,
    rotateAngle,
    // The preview wins over the pointer: Enter has moved the cursor to a typed
    // point and it stays there until the point is placed or cancelled.
    hover: coordPreview ?? hover,
    shiftHeld,
    unitLabel: unit.label,
    formatLength,
  }
  sceneRef.current = scene

  const paint = useCallback((override?: Partial<PlanScene>) => {
    const canvas = canvasRef.current
    const host = hostRef.current
    const current = sceneRef.current
    if (!canvas || !host || !current) return
    const width = override?.width ?? host.clientWidth
    const heightPx = override?.height ?? host.clientHeight
    if (canvas.width !== width) canvas.width = width
    if (canvas.height !== heightPx) canvas.height = heightPx
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    drawPlan(ctx, { ...current, ...override, width, height: heightPx })
  }, [])

  useLayoutEffect(() => {
    paint()
    // The 3D toolbar owns the 旋转吸附 flag; re-read it here so a toggle made
    // over there lights this toolbar's button too.
    const live = isRotationSnapOn()
    if (live !== rotSnap) setRotSnap(live)
  })

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const ro = new ResizeObserver(() => paint())
    ro.observe(host)
    return () => ro.disconnect()
  }, [paint])

  // --- selection ----------------------------------------------------------
  const select = useCallback((uid: string | null, toggle: boolean) => {
    setSelectedUids((prev) => {
      if (!uid) return new Set()
      if (!toggle) return new Set([uid])
      const next = new Set(prev)
      if (next.has(uid)) next.delete(uid)
      else next.add(uid)
      return next
    })
    // The app's own selection, so the inspector beside the plan follows the
    // click. `requestSelect` reaches it through the feature-tree row, which is
    // the real selection path rather than a simulation of one.
    requestSelect(uid)
  }, [])

  // --- pointer ------------------------------------------------------------
  function localPoint(e: { clientX: number; clientY: number }): Point {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }

  function beginDrag(next: Drag) {
    dragRef.current = next
    setDragging(true)
  }

  function onPointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    const c = localPoint(e)
    const w = toWorld(c.x, c.y)
    setShiftHeld(e.shiftKey)

    if (e.button === 1 || e.button === 2) {
      beginDrag({
        kind: 'pan',
        clientX: e.clientX,
        clientY: e.clientY,
        offsetX: view.offsetX,
        offsetY: view.offsetY,
      })
      return
    }

    if (tool === 'draw' && !closed) {
      const p = snapPoint(w, { constrain: true })
      if (vertices.length >= 3) {
        const first = toCanvas(vertices[0]![0], vertices[0]![1])
        const at = toCanvas(p[0], p[1])
        if (Math.hypot(first.x - at.x, first.y - at.y) < CLOSE_PX) {
          setClosed(true)
          setTool('select')
          return
        }
      }
      // A click on a vertex that is not the first REMOVES it, so a misplaced
      // point can be taken back without leaving draw mode.
      for (let i = 1; i < vertices.length; i += 1) {
        const pv = toCanvas(vertices[i]![0], vertices[i]![1])
        if (Math.hypot(c.x - pv.x, c.y - pv.y) < HIT_PX) {
          setVertices((all) => all.filter((_, j) => j !== i))
          return
        }
      }
      setVertices((all) => [...all, p])
      return
    }

    if (tool === 'select') {
      const ring = hitRotateRing(models, c.x, c.y, toCanvas)
      if (ring) {
        const targets = selectedUids.has(ring.model.uid)
          ? models.filter((m) => selectedUids.has(m.uid) && m.rotatable).map((m) => m.uid)
          : [ring.model.uid]
        const pivots = new Map<string, Point>()
        for (const m of models) {
          if (!targets.includes(m.uid)) continue
          const r = rotateRing(m, toCanvas)
          if (r) pivots.set(m.uid, r.pivot)
        }
        setRotateHoverUid(null)
        setRotatingUid(ring.model.uid)
        setRotateAngle(ring.ring.angle)
        beginDrag({
          kind: 'rotate',
          origins: dragOrigins(models, targets),
          pivots,
          startAngle: ring.ring.angle,
          // Measured in CANVAS space, matching where the handle is drawn — the
          // ring turns with the pointer only if both use the same sense.
          grabAngle: Math.atan2(c.y - ring.ring.centre.y, c.x - ring.ring.centre.x),
          centre: ring.ring.centre,
        })
        return
      }

      const hit = hitModel(models, c.x, c.y, toCanvas)
      if (hit) {
        const alreadySelected = selectedUids.has(hit.uid)
        if (alreadySelected && hit.movable) {
          const movers = models
            .filter((m) => selectedUids.has(m.uid) && m.movable)
            .map((m) => m.uid)
          beginDrag({ kind: 'model', origins: dragOrigins(models, movers), start: w })
          return
        }
        select(hit.uid, e.ctrlKey || e.metaKey || e.shiftKey)
        setSelectedVertex(-1)
        setSelectedEdge(-1)
        setBoxSel({ vertices: [], edges: [] })
        return
      }

      if (selectedUids.size) select(null, false)

      for (let i = 0; i < vertices.length; i += 1) {
        const p = toCanvas(vertices[i]![0], vertices[i]![1])
        if (Math.hypot(c.x - p.x, c.y - p.y) < HIT_PX) {
          setSelectedVertex(i)
          setSelectedEdge(-1)
          beginDrag({ kind: 'vertex', index: i })
          return
        }
      }
      const edgeCount = closed ? vertices.length : vertices.length - 1
      for (let i = 0; i < edgeCount; i += 1) {
        const a = toCanvas(vertices[i]![0], vertices[i]![1])
        const b = toCanvas(
          vertices[(i + 1) % vertices.length]![0],
          vertices[(i + 1) % vertices.length]![1],
        )
        if (pointToSegmentDist(c.x, c.y, a.x, a.y, b.x, b.y) < EDGE_HIT_PX) {
          setSelectedEdge(i)
          setSelectedVertex(-1)
          beginDrag({ kind: 'edge', index: i, last: w })
          return
        }
      }

      setSelectedVertex(-1)
      setSelectedEdge(-1)
      setBoxSel({ vertices: [], edges: [] })
      setBand({ a: c, b: c })
      beginDrag({ kind: 'band', start: c })
    }
  }

  /**
   * Place the vertex the coordinate boxes are pointing at.
   *
   * The preview if Enter set one, otherwise wherever the snapped pointer is —
   * so Space alone works as a keyboard equivalent of a click, which is what the
   * legacy `_confirmCoordVertex` does when no preview has been typed.
   */
  const placeCoordVertex = useCallback(() => {
    const at = coordPreview ?? hover
    if (!at || closed) return
    const placement = coordPlacement(vertices, at, toCanvas, CLOSE_PX)
    setCoordPreview(null)
    if (placement.kind === 'close') {
      setClosed(true)
      setTool('select')
      return
    }
    setVertices((all) => [...all, placement.at])
  }, [coordPreview, hover, closed, vertices, toCanvas])

  function onPointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (dragRef.current) return
    const c = localPoint(e)
    if (tool === 'draw' && !closed) {
      setHover(snapPoint(toWorld(c.x, c.y), { constrain: true }))
      return
    }
    const ring = hitRotateRing(models, c.x, c.y, toCanvas, RING_HOVER_PX)
    const next = ring?.model.uid ?? null
    if (next !== rotateHoverUid) setRotateHoverUid(next)
  }

  // Move and release are on the WINDOW while a drag runs, not on the canvas.
  // Pointer capture is the usual answer, but the headless browser used to verify
  // this app does not implement it, and a drag that stops the moment the pointer
  // leaves the canvas is untestable there — and unpleasant with a rack near an
  // edge.
  useEffect(() => {
    if (!dragging) return
    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const c = localPoint(e)
      const w = toWorld(c.x, c.y)

      if (drag.kind === 'pan') {
        setView((v) => ({
          ...v,
          offsetX: drag.offsetX - (e.clientX - drag.clientX) / v.scale,
          offsetY: drag.offsetY + (e.clientY - drag.clientY) / v.scale,
        }))
        return
      }

      if (drag.kind === 'vertex') {
        const p = snapPoint(w, { dragVertex: drag.index })
        setVertices((all) => all.map((old, i) => (i === drag.index ? p : old)))
        return
      }

      if (drag.kind === 'edge') {
        // Snap the edge's FIRST vertex, then carry the second by the same
        // delta, so the wall keeps its length and angle while it slides.
        setVertices((all) => {
          const i = drag.index
          const j = (i + 1) % all.length
          const a = all[i]!
          const raw: Vertex2 = [a[0] + (w[0] - drag.last[0]), a[1] + (w[1] - drag.last[1])]
          const snapped = snapPoint(raw, { dragVertex: i })
          const dx = snapped[0] - a[0]
          const dy = snapped[1] - a[1]
          return all.map((v, k) => {
            if (k === i) return [v[0] + dx, v[1] + dy] as Vertex2
            if (k === j) return [v[0] + dx, v[1] + dy] as Vertex2
            return v
          })
        })
        drag.last = w
        return
      }

      if (drag.kind === 'model') {
        const dx = w[0] - drag.start[0]
        const dy = w[1] - drag.start[1]
        const moving = new Set(drag.origins.map((o) => o.uid))
        const boxes = snapBoxes(models, moving)
        const lead = drag.origins[0]
        const leadModel = lead ? models.find((m) => m.uid === lead.uid) : undefined
        let sdx = dx
        let sdy = dy
        if (lead && leadModel) {
          const [sx, sy] = snapModelAnchor(lead.x + dx, lead.z + dy, {
            modes: snap,
            gridSpacing,
            scale: view.scale,
            boxes,
            size: { w: leadModel.size.x, d: leadModel.size.z },
          })
          // One snap for the whole selection, applied as a shared delta: a
          // per-model snap would tear a selected row apart.
          sdx = sx - lead.x
          sdy = sy - lead.z
        }
        placeLive(drag.origins.map((o) => ({ uid: o.uid, x: o.x + sdx, z: o.z + sdy })))
        setLiveTick((n) => n + 1)
        return
      }

      if (drag.kind === 'rotate') {
        const now = Math.atan2(c.y - drag.centre.y, c.x - drag.centre.x)
        const target = snapDelta(
          drag.startAngle + (now - drag.grabAngle),
          e.shiftKey,
          isRotationSnapOn(),
        )
        const delta = target - drag.startAngle
        setRotateAngle(target)
        const placements: PlanPlacement[] = []
        for (const origin of drag.origins) {
          const pivot = drag.pivots.get(origin.uid)
          if (!pivot) continue
          const moved = rotateAnchor(
            { x: origin.x, y: 0, z: origin.z },
            { x: pivot.x, y: 0, z: pivot.y },
            'y',
            delta,
          )
          placements.push({
            uid: origin.uid,
            x: moved.x,
            z: moved.z,
            quaternion: rotateQuaternion(origin.quaternion, 'y', delta),
          })
        }
        placeLive(placements)
        setLiveTick((n) => n + 1)
        return
      }

      if (drag.kind === 'band') {
        setBand({ a: drag.start, b: c })
        setBoxSel(boxSelect(vertices, closed, toCanvas, drag.start, c))
      }
    }

    const onUp = () => {
      const drag = dragRef.current
      dragRef.current = null
      setDragging(false)
      if (!drag) return

      if (drag.kind === 'vertex' || drag.kind === 'edge') {
        // The legacy regenerates the room the moment a wall is dropped
        // (`_onMouseUp`, :1826), so the walls follow the outline without
        // waiting for Apply. One `edit` per drag, so one undo step.
        if (closed && vertices.length >= 3) applyRoom(vertices, height)
        return
      }

      if (drag.kind === 'model') {
        const live = store.getProject()
        const placements: PlanPlacement[] = drag.origins.flatMap((o) => {
          const hit = live ? store.findConstruction(live, o.uid) : null
          const at = hit?.model.geometry?.location
          return at ? [{ uid: o.uid, x: at.x, z: at.z }] : []
        })
        const start = new Map(drag.origins.map((o) => [o.uid, o]))
        const moved = placements.some((p) => {
          const o = start.get(p.uid)
          return !o || Math.abs(p.x - o.x) > 1e-9 || Math.abs(p.z - o.z) > 1e-9
        })
        if (moved) {
          commitPlacement(
            '2dMoveModel',
            `${placements.length} model(s)`,
            drag.origins,
            placements,
          )
        }
        return
      }

      if (drag.kind === 'rotate') {
        const live = store.getProject()
        const placements: PlanPlacement[] = drag.origins.flatMap((o) => {
          const hit = live ? store.findConstruction(live, o.uid) : null
          const geometry = hit?.model.geometry
          const at = geometry?.location
          return at
            ? [
                {
                  uid: o.uid,
                  x: at.x,
                  z: at.z,
                  quaternion: geometry?.quaternion ?? o.quaternion,
                },
              ]
            : []
        })
        setRotatingUid(null)
        setRotateAngle(null)
        if (placements.length) {
          commitPlacement('2dRotateModel', `${placements.length} model(s)`, drag.origins, placements)
        }
        return
      }

      if (drag.kind === 'band') {
        // The band itself goes, but the picked vertices and edges SURVIVE the
        // release — Delete acts on them next, which is the point of the band.
        setBand(null)
      }
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  })

  function onWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    const c = localPoint(e)
    const before = toWorld(c.x, c.y)
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
    setView((v) => {
      const scale = clampScale(v.scale * factor)
      const canvas = canvasRef.current
      const after = canvasToWorld(
        { ...v, scale },
        rotation,
        canvas?.width ?? 0,
        canvas?.height ?? 0,
        c.x,
        c.y,
      )
      // Zoom about the CURSOR, not the canvas centre: the offset correction is
      // what keeps the point under the pointer still.
      return { scale, offsetX: v.offsetX + (before[0] - after.x), offsetY: v.offsetY + (before[1] - after.y) }
    })
  }

  // --- keyboard -----------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const tag = target?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || target?.isContentEditable) return
      setShiftHeld(e.shiftKey)

      // Tab hands the keyboard to the coordinate boxes, Space places whatever
      // they are pointing at. Both are draw-mode only — Tab elsewhere is the
      // browser's own focus order and taking it would trap the keyboard.
      if (e.key === 'Tab' && tool === 'draw' && !closed) {
        e.preventDefault()
        coordXRef.current?.focus()
        coordXRef.current?.select()
        return
      }
      if (e.key === ' ' && tool === 'draw' && !closed) {
        e.preventDefault()
        placeCoordVertex()
        return
      }
      if (e.key === 'Enter' && !closed && vertices.length >= 3) {
        e.preventDefault()
        setClosed(true)
        setTool('select')
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setCoordPreview(null)
        setTool('select')
        return
      }
      if (e.key !== 'Delete' && e.key !== 'Backspace') return

      if (boxSel.vertices.length > 0) {
        e.preventDefault()
        setVertices((all) => deleteVertices(all, boxSel.vertices))
        setClosed(false)
        setBoxSel({ vertices: [], edges: [] })
        setSelectedVertex(-1)
        setSelectedEdge(-1)
        return
      }
      if (selectedVertex >= 0) {
        e.preventDefault()
        setVertices((all) => {
          const next = all.filter((_, i) => i !== selectedVertex)
          if (next.length < 3) setClosed(false)
          return next.length < 2 ? [] : next
        })
        setSelectedVertex(-1)
        return
      }
      if (selectedEdge >= 0) {
        e.preventDefault()
        setVertices((all) => breakEdge(all, selectedEdge))
        setSelectedEdge(-1)
        setClosed(false)
      }
    }
    const onKeyUp = (e: KeyboardEvent) => setShiftHeld(e.shiftKey)
    window.addEventListener('keydown', onKey)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [closed, vertices, selectedVertex, selectedEdge, boxSel, tool, placeCoordVertex])

  // --- apply --------------------------------------------------------------
  const canApply = closed && vertices.length >= 3 && height > 0

  const applyRoom = useCallback((ring: Vertex2[], h: number) => {
    const live = store.getProject()
    if (!live || ring.length < 3) return
    const room = generateRoom(live, ring, h)
    store.replaceRoom(
      ring.map((v) => [v[0], v[1]] as [number, number]),
      h,
      room.replaces,
      [...room.walls, room.floor, room.ceiling],
    )
  }, [])

  function apply() {
    if (!canApply) return
    applyRoom(vertices, height)
    onClose()
  }

  function useRectangle(width: number, depth: number) {
    setVertices(rectangleFootprint(width, depth))
    setClosed(true)
    setTool('select')
    setTimeout(zoomToFit, 0)
  }

  // --- the `window.room2d` handle ----------------------------------------
  // Rebuilt every render so each call sees current state, but published ONCE
  // through a ref: `registerEditor` in a `[]` effect would otherwise capture
  // the first render's closures and `setAnnotations(true)` would toggle a
  // state nothing reads any more.
  const handleRef = useRef<Room2DHandle | null>(null)
  handleRef.current = {
    zoomToFit,
    setShowAxes: (show) => setShowAxes(!!show),
    setAnnotations: (show) => setShowAnnotations(!!show),
    setBgColor: (color) => setBgColor(color || DEFAULT_BG),
    setViewRotation: (deg) => {
      const next = (((deg % 360) + 360) % 360) as number
      setRotation(next)
      const { w, h } = size()
      const fit = fitView(extentPoints(vertices, models), next, w, h)
      if (fit) setView(fit)
    },
    rotateView90: () => {
      const next = (rotation + 90) % 360
      setRotation(next)
      const { w, h } = size()
      const fit = fitView(extentPoints(vertices, models), next, w, h)
      if (fit) setView(fit)
    },
    setTool: (next) => setTool(next),
    toggleGridSnap: () => {
      const next = !snap.grid
      setSnap(next ? { grid: true, vertex: false, edge: false } : { ...snap, grid: false })
      // Grid snap excludes the ROTATION snap too, not just the other two
      // (`toggleGridSnap`, room_2d_editor.js:2103) — a rack quantised to whole
      // degrees and to the grid at once cannot satisfy both.
      if (next && isRotationSnapOn()) {
        setRotationSnap(false)
        setRotSnap(false)
      }
    },
    toggleVertexSnap: () => setSnap((s) => ({ ...s, vertex: !s.vertex, grid: false })),
    toggleEdgeAlign: () => setSnap((s) => ({ ...s, edge: !s.edge, grid: false })),
    toggleRotationSnap: () => {
      const next = !isRotationSnapOn()
      setRotationSnap(next)
      setRotSnap(next)
      if (next) setSnap((s) => ({ ...s, grid: false }))
    },
    setRotationSnap: (on) => setRotSnap(!!on),
    setGridSpacing: (metres) => setGridSpacing(Math.max(0.001, metres)),
    editGridBadge: () => editGridBadge(),
    // DISPLAY units in, metres stored — `room2d.setHeight` is the legacy's
    // (`fromDisplayLength`, room_2d_editor.js:2172), and the box beside it
    // shows the same number, so a project authored in mm takes 3800 here.
    setHeight: (value) => {
      const metres = fromDisplay(value, unitId)
      if (Number.isFinite(metres) && metres > 0) setHeight(metres)
    },
    setVertexCoord: (index, axis, value) => {
      if (!Number.isFinite(value)) return
      setVertices((all) =>
        all.map((p, i) =>
          i === index ? ((axis === 0 ? [value, p[1]] : [p[0], value]) as Vertex2) : p,
        ),
      )
    },
    clearAll: () => {
      setVertices([])
      setClosed(false)
      setSelectedVertex(-1)
      setSelectedEdge(-1)
      setTool('draw')
    },
    exportCanvasPNG: (width, heightPx) => exportPNG(width, heightPx),
    getDebugState: () => {
      const canvas = canvasRef.current
      return {
        active: true,
        tool,
        closed,
        vertices: vertices.length,
        view: { ...view },
        viewRotation: rotation,
        canvasWidth: canvas?.width ?? 0,
        canvasHeight: canvas?.height ?? 0,
        // Beyond the legacy's key set: the 2D selection is what decides
        // whether the next drag moves a rack, and it is otherwise only
        // visible as a dashed outline on a canvas.
        selectedUids: [...selectedUids],
        // The footprint itself, not just its length: the legacy reports a
        // count, and a count cannot show that an edge-break re-rolled the ring.
        plane: vertices.map((v) => [v[0], v[1]]),
        selectedVertex,
        selectedEdge,
        boxSelected: { vertices: boxSel.vertices, edges: boxSel.edges },
        snap: { ...snap, rotation: isRotationSnapOn() },
        showAxes,
        showAnnotations,
        bgColor,
        gridSpacing,
        height,
        // The typed-coordinate cursor. A preview is invisible in `plane` —
        // it is a point the user has aimed at and not yet placed — so without
        // these two a verification run cannot tell "Enter previewed" from
        // "Enter did nothing".
        coordPreview: coordPreview ? [coordPreview[0], coordPreview[1]] : null,
        coordFocused,
        aisleLabels: computeAnnotations(models).labels,
      }
    },
    exit: (applyIt) => {
      if (applyIt && canApply) applyRoom(vertices, height)
      onClose()
    },
  }

  /**
   * Export the plan at an explicit size.
   *
   * The canvas is resized, re-fitted and repainted for the shot, then put back
   * — the legacy does the same (`exportCanvasPNG`, :2082). Fitting matters: a
   * 3840x2160 export of a view framed for a 900px panel would show the room in
   * one corner.
   */
  function exportPNG(width?: number, heightPx?: number): string | null {
    const canvas = canvasRef.current
    if (!canvas) return null
    const w = width || 3840
    const h = heightPx || 2160
    const fit = fitView(extentPoints(vertices, models), rotation, w, h) ?? view
    paint({ width: w, height: h, view: fit })
    const url = canvas.toDataURL('image/png')
    paint()
    return url
  }

  function editGridBadge() {
    const current = formatLength(gridSpacing)
    const typed = window.prompt(`${t('toolbar.grid_spacing')} (${unit.label}):`, current)
    if (typed === null) return
    const value = Number.parseFloat(typed)
    if (Number.isFinite(value) && value > 0) setGridSpacing(value * unit.scale)
  }

  useLayoutEffect(() => {
    // A second editor must not leave an API-mounted one behind it, invisible
    // and holding the handle — but the API-mounted one is not its own second.
    if (!standalone) dropStandaloneIfAny()
    return registerEditor({
      zoomToFit: () => handleRef.current!.zoomToFit(),
      setShowAxes: (v) => handleRef.current!.setShowAxes(v),
      setAnnotations: (v) => handleRef.current!.setAnnotations(v),
      setBgColor: (v) => handleRef.current!.setBgColor(v),
      setViewRotation: (v) => handleRef.current!.setViewRotation(v),
      rotateView90: () => handleRef.current!.rotateView90(),
      setTool: (v) => handleRef.current!.setTool(v),
      toggleGridSnap: () => handleRef.current!.toggleGridSnap(),
      toggleVertexSnap: () => handleRef.current!.toggleVertexSnap(),
      toggleEdgeAlign: () => handleRef.current!.toggleEdgeAlign(),
      toggleRotationSnap: () => handleRef.current!.toggleRotationSnap(),
      setRotationSnap: (v) => handleRef.current!.setRotationSnap(v),
      setGridSpacing: (v) => handleRef.current!.setGridSpacing(v),
      editGridBadge: () => handleRef.current!.editGridBadge(),
      setHeight: (v) => handleRef.current!.setHeight(v),
      setVertexCoord: (i, axis, v) => handleRef.current!.setVertexCoord(i, axis, v),
      clearAll: () => handleRef.current!.clearAll(),
      exportCanvasPNG: (w, h) => handleRef.current!.exportCanvasPNG(w, h),
      getDebugState: () => handleRef.current!.getDebugState(),
      exit: (v) => handleRef.current!.exit(v),
    })
  }, [])

  /**
   * The right column is BORROWED while the editor is up, as the legacy borrows
   * `#prop-content` (`room_2d_editor.js:1955`).
   *
   * The claim is what tells the shell to put the 3D inspector away; the host is
   * the element it renders in its place. A token per editor instance, because
   * an API-mounted editor being replaced by a button-opened one tears down
   * after the new one has already claimed.
   */
  const slotToken = useMemo(() => ({}), [])
  useEffect(() => {
    claimRoom2dPanel(slotToken)
    return () => releaseRoom2dPanel(slotToken)
  }, [slotToken])
  const panelHost = useSyncExternalStore(subscribeRoom2dPanel, getRoom2dPanelHost)

  const ccw = vertices.length >= 3 ? signedArea2(vertices) >= 0 : true
  const area = vertices.length >= 3 ? polygonArea(vertices) : 0

  const sideColumn = (
    <div className="room2d-side">
      <div className="prop-group">
        <div className="prop-title">{t('room2d.room_settings')}</div>
        <div className="prop-row">
          <label>
            {t('label.room_height')} ({unit.label})
          </label>
          <NumberField
            id="room2d-height"
            value={toDisplay(height, unitId)}
            step={unit.step}
            min={unit.step}
            onCommit={(v) => handleRef.current?.setHeight(v)}
          />
        </div>
      </div>

      {selectedVertex >= 0 && selectedVertex < vertices.length && (
        <div className="prop-group" id="room2d-vertex-props">
          {/* The legacy hard-codes these two panels' labels in English
              (room_2d_editor.js:1968-1985) — there is no dictionary key for
              them, and the nearest existing keys mean something else
              entirely: label.beam_length is a light beam's length. */}
          <div className="prop-title">Selected Vertex #{selectedVertex + 1}</div>
          <div className="prop-row">
            <label>X</label>
            <NumberField
              value={vertices[selectedVertex]![0]}
              step={0.1}
              onCommit={(x) => handleRef.current?.setVertexCoord(selectedVertex, 0, x)}
            />
          </div>
          <div className="prop-row">
            <label>Y</label>
            <NumberField
              value={vertices[selectedVertex]![1]}
              step={0.1}
              onCommit={(y) => handleRef.current?.setVertexCoord(selectedVertex, 1, y)}
            />
          </div>
        </div>
      )}

      {selectedEdge >= 0 && selectedEdge < vertices.length && closed && (
        <div className="prop-group" id="room2d-edge-props">
          <div className="prop-title">Selected Edge #{selectedEdge + 1}</div>
          <div className="prop-row">
            <label>Length</label>
            <span className="value" id="room2d-edge-length">
              {Math.hypot(
                vertices[(selectedEdge + 1) % vertices.length]![0] - vertices[selectedEdge]![0],
                vertices[(selectedEdge + 1) % vertices.length]![1] - vertices[selectedEdge]![1],
              ).toFixed(3)}{' '}
              m
            </span>
          </div>
          <div className="prop-row">
            <label>{t('room2d.edge_angle')}</label>
            <span className="value" id="room2d-edge-angle">
              {edgeAngleDeg(vertices, selectedEdge).toFixed(1)}°
            </span>
          </div>
        </div>
      )}

      <div className="prop-group" id="room2d-polygon-info">
        <div className="prop-title">{t('room2d.polygon_info')}</div>
        <div className="prop-row">
          <label>{t('room2d.vertices_count')}</label>
          <span className="value" id="room2d-vertex-count">
            {vertices.length}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('room2d.perimeter')}</label>
          <span className="value" id="room2d-perimeter">
            {vertices.length >= 2 ? `${perimeter(vertices).toFixed(2)} m` : '—'}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('room2d.area')}</label>
          <span className="value" id="room2d-area">
            {vertices.length >= 3 ? `${area.toFixed(2)} m²` : '—'}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('label.winding')}</label>
          <span className="value" style={{ color: ccw ? 'var(--highlight)' : 'var(--hint-color)' }}>
            {ccw ? 'CCW' : 'CW'}
          </span>
        </div>
        <div className="prop-hint">{ccw ? t('hint.winding_ccw') : t('hint.winding_cw')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('title.quick_rectangle')}</div>
        <div className="prop-hint">{t('hint.quick_rectangle')}</div>
        <RectangleForm onCreate={useRectangle} />
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('title.vertices')}</div>
        <div className="room2d-vertex-list">
          {vertices.map((v, i) => (
            <div className="prop-row" key={i}>
              <label>{i + 1}</label>
              <NumberField
                value={v[0]}
                step={0.1}
                onCommit={(x) => handleRef.current?.setVertexCoord(i, 0, x)}
              />
              <NumberField
                value={v[1]}
                step={0.1}
                onCommit={(y) => handleRef.current?.setVertexCoord(i, 1, y)}
              />
              <button
                className="node-action-btn"
                title={t('tooltip.delete')}
                onClick={() => setVertices((all) => all.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
          ))}
          {vertices.length === 0 && <div className="empty">{t('hint.draw_footprint')}</div>}
        </div>
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('title.will_generate')}</div>
        <div className="prop-hint">
          {t('hint.will_generate', { walls: countWalls(vertices), height: height.toFixed(2) })}
        </div>
        {!canApply && <div className="prop-hint">{t('warn.polygon_not_closed')}</div>}
      </div>

      <div className="prop-group">
        <div className="prop-hint" id="room2d-drag-hint">
          {t('inspector.move_hint')}
        </div>
      </div>
    </div>
  )

  return (
    <div className="room2d" id="room2d-editor">
      <div className="room2d-toolbar" id="room2d-toolbar">
        <button
          className={`tool-btn${tool === 'draw' ? ' active' : ''}`}
          id="room2d-btn-draw"
          onClick={() => setTool('draw')}
        >
          🖊 {t('label.draw_mode')}
        </button>
        <button
          className={`tool-btn${tool === 'select' ? ' active' : ''}`}
          id="room2d-btn-select"
          onClick={() => setTool('select')}
        >
          ✋ {t('label.select_mode')}
        </button>
        <span className="tool-sep" />
        <button
          className={`tool-btn${snap.grid ? ' active' : ''}`}
          id="room2d-btn-gridsnap"
          onClick={() => handleRef.current?.toggleGridSnap()}
        >
          📐 {t('label.grid_snap')}
        </button>
        <button
          className={`tool-btn${snap.vertex ? ' active' : ''}`}
          id="room2d-btn-vtxsnap"
          onClick={() => handleRef.current?.toggleVertexSnap()}
        >
          ⊞ {t('label.vertex_snap')}
        </button>
        <button
          className={`tool-btn${snap.edge ? ' active' : ''}`}
          id="room2d-btn-edgealign"
          onClick={() => handleRef.current?.toggleEdgeAlign()}
        >
          ═ {t('label.edge_align')}
        </button>
        <button
          className={`tool-btn${rotSnap ? ' active' : ''}`}
          id="room2d-btn-rotsnap"
          onClick={() => handleRef.current?.toggleRotationSnap()}
        >
          🔄 {t('label.rotation_snap')}
        </button>
        <div className="tool-inline">
          <span>{t('toolbar.grid_spacing')}</span>
          <input
            id="room2d-grid-slider"
            type="range"
            min={10}
            max={2000}
            step={10}
            value={Math.round(gridSpacing * 1000)}
            onChange={(e) => setGridSpacing(Number(e.target.value) / 1000)}
          />
          <span
            className="badge"
            id="room2d-grid-badge"
            style={{ cursor: 'pointer' }}
            onDoubleClick={editGridBadge}
          >
            {formatLength(gridSpacing)} {unit.label}
          </span>
        </div>
        <span className="tool-sep" />
        <button className="tool-btn" id="room2d-btn-fit" title={t('tooltip.zoom_to_fit')} onClick={zoomToFit}>
          ⊞ {t('btn.zoom_to_fit')}
        </button>
        <button
          className="tool-btn"
          id="room2d-btn-rot90"
          title={t('tooltip.rotate_90')}
          onClick={() => handleRef.current?.rotateView90()}
        >
          {t('btn.rotate_90')}
        </button>
        <button
          className={`tool-btn${showAxes ? ' active' : ''}`}
          id="room2d-btn-axes"
          onClick={() => setShowAxes((v) => !v)}
        >
          ✛ XZ
        </button>
        <button
          className={`tool-btn${showAnnotations ? ' active' : ''}`}
          id="room2d-btn-annot"
          onClick={() => setShowAnnotations((v) => !v)}
        >
          🏷 HOT/COLD
        </button>
        <input
          type="color"
          id="room2d-bg-color"
          value={bgColor}
          onChange={(e) => setBgColor(e.target.value)}
          style={{ width: 26, height: 22, padding: 0, border: '1px solid var(--border)', background: 'none' }}
        />
        <button className="tool-btn" id="room2d-btn-png" onClick={() => downloadPNG(exportPNG())}>
          {t('btn.screenshot')}
        </button>
        <span className="tool-sep" />
        <button
          className="tool-btn"
          id="room2d-btn-clear"
          onClick={() => handleRef.current?.clearAll()}
          style={{ color: 'var(--danger)' }}
        >
          {t('label.clear_all')}
        </button>
        <button className="tool-btn" id="room2d-btn-cancel" onClick={onClose}>
          {t('common.cancel')}
        </button>
        <button
          className={`tool-btn${canApply ? ' active' : ''}`}
          id="room2d-btn-apply"
          disabled={!canApply}
          onClick={apply}
        >
          ✅ {t('btn.apply_room')}
        </button>
      </div>

      <div className="room2d-canvas-host" ref={hostRef}>
        <canvas
          id="room2d-canvas"
          ref={canvasRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onWheel={onWheel}
          onContextMenu={(e) => e.preventDefault()}
          style={{ cursor: dragCursor(tool, closed, rotateHoverUid, rotatingUid) }}
        />
        {tool === 'draw' && !closed && (
          <CoordTooltip
            at={coordPreview ?? hover}
            anchor={toCanvas}
            unit={unitId}
            unitLabel={unit.label}
            focused={coordFocused}
            xRef={coordXRef}
            yRef={coordYRef}
            onFocusChange={setCoordFocused}
            onPreview={(x, y) => {
              const p = parseCoordEntry(x, y, unitId)
              if (p) setCoordPreview(p)
            }}
            onPlace={placeCoordVertex}
            onCancel={() => setCoordPreview(null)}
          />
        )}
      </div>

      {/* The plan's properties go in the app's right column, over the 3D
          inspector, because that is where the legacy writes them
          (room_2d_editor.js:1955). Rendered here only if nothing published a
          host — switching to a tab that replaces the right column takes it
          away, and the controls have to stay reachable. */}
      {panelHost ? createPortal(sideColumn, panelHost) : sideColumn}
    </div>
  )
}

/**
 * The X / Y / Z boxes that ride the draw cursor.
 *
 * Ported from `_createCoordTooltip` + `_updateCoordTooltip`
 * (web/room_2d_editor.js:1064-1198), ids included, so one selector reaches it
 * in either app.
 *
 * The boxes follow the cursor until one of them has focus and freeze while it
 * does — a value being typed must survive the next mouse move, and the panel
 * must stop chasing the pointer out from under the caret.
 */
function CoordTooltip({
  at,
  anchor,
  unit,
  unitLabel,
  focused,
  xRef,
  yRef,
  onFocusChange,
  onPreview,
  onPlace,
  onCancel,
}: {
  at: Vertex2 | null
  anchor: (x: number, y: number) => Point
  unit: LengthUnit
  unitLabel: string
  focused: boolean
  xRef: React.MutableRefObject<HTMLInputElement | null>
  yRef: React.MutableRefObject<HTMLInputElement | null>
  onFocusChange: (v: boolean) => void
  onPreview: (x: string, y: string) => void
  onPlace: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [fields, setFields] = useState({ x: '0.0', y: '0.0', z: '0.0' })
  const [pos, setPos] = useState<Point>({ x: 0, y: 0 })

  // No dependency list, and every write bails out when the value has not
  // changed: `at` is a fresh array each render, so a dependency on it would
  // re-run for ever.
  useEffect(() => {
    if (focused || !at) return
    const next = coordDisplay(at, unit)
    setFields((prev) =>
      prev.x === next.x && prev.y === next.y && prev.z === next.z ? prev : next,
    )
    const p = anchor(at[0], at[1])
    const left = p.x + COORD_TOOLTIP_OFFSET
    const top = p.y + COORD_TOOLTIP_OFFSET
    setPos((prev) => (prev.x === left && prev.y === top ? prev : { x: left, y: top }))
  })

  const onKeyDown = (field: 'x' | 'y') => (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      onPreview(fields.x, fields.y)
      return
    }
    if (e.key === ' ') {
      // Places the PREVIEW, not whatever is half-typed in the boxes — the
      // legacy `_confirmCoordVertex` reads `_coordPreviewPos`, never the
      // inputs. Enter is what commits a typed pair to the preview, which is why
      // the hint spells the two steps out.
      e.preventDefault()
      e.stopPropagation()
      onPlace()
      return
    }
    if (e.key === 'Tab') {
      e.preventDefault()
      e.stopPropagation()
      const other = nextCoordField(field) === 'x' ? xRef.current : yRef.current
      other?.focus()
      other?.select()
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      e.currentTarget.blur()
      onCancel()
    }
  }

  const inputStyle: React.CSSProperties = {
    width: 62,
    background: 'rgba(0,0,0,0.4)',
    border: '1px solid rgba(0,212,255,0.25)',
    color: '#fff',
    font: '11px "Share Tech Mono", monospace',
    padding: '1px 4px',
    margin: '0 2px',
    textAlign: 'right',
  }

  // Deferred, and re-read from `document.activeElement` rather than from the
  // event's `relatedTarget` — `_isCoordInputFocused` (room_2d_editor.js:1059)
  // does the same, for two reasons. Blur fires BEFORE focus lands on the next
  // box, so an immediate check would unfreeze between X and Y; and a
  // window-level blur carries no `relatedTarget` at all, which would unfreeze
  // the boxes while the caret is still sitting in one.
  const trackFocus = () => {
    window.setTimeout(() => {
      const active = document.activeElement
      onFocusChange(active === xRef.current || active === yRef.current)
    }, 10)
  }

  return (
    <div
      id="room2d-coord-tooltip"
      style={{
        position: 'absolute',
        left: pos.x,
        top: pos.y,
        pointerEvents: 'auto',
        zIndex: 50,
        background: 'rgba(16,21,28,0.92)',
        border: '1px solid rgba(0,212,255,0.35)',
        padding: '4px 6px',
        font: '11px "Share Tech Mono", monospace',
        color: '#8be9fd',
        whiteSpace: 'nowrap',
      }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <span style={{ color: 'rgba(0,212,255,0.6)' }}>X</span>
      <input
        id="r2d-coord-x"
        type="number"
        step="any"
        ref={xRef}
        style={inputStyle}
        value={fields.x}
        onChange={(e) => setFields((f) => ({ ...f, x: e.target.value }))}
        onFocus={() => onFocusChange(true)}
        onBlur={trackFocus}
        onKeyDown={onKeyDown('x')}
      />{' '}
      <span style={{ color: 'rgba(0,212,255,0.6)' }}>Y</span>
      <input
        id="r2d-coord-y"
        type="number"
        step="any"
        ref={yRef}
        style={inputStyle}
        value={fields.y}
        onChange={(e) => setFields((f) => ({ ...f, y: e.target.value }))}
        onFocus={() => onFocusChange(true)}
        onBlur={trackFocus}
        onKeyDown={onKeyDown('y')}
      />{' '}
      <span style={{ color: 'rgba(0,212,255,0.6)' }}>Z</span>
      <input
        id="r2d-coord-z"
        type="number"
        step="any"
        readOnly
        tabIndex={-1}
        style={{ ...inputStyle, opacity: 0.5, pointerEvents: 'none' }}
        value={fields.z}
        onChange={() => undefined}
      />{' '}
      <span style={{ color: 'rgba(0,212,255,0.4)', fontSize: 9 }}>{unitLabel}</span>
      <div
        style={{
          marginTop: 3,
          fontSize: 9,
          color: 'rgba(0,212,255,0.35)',
          lineHeight: 1.4,
          whiteSpace: 'normal',
          maxWidth: 220,
        }}
      >
        {t('hint.coord_tooltip_keys')}
      </div>
    </div>
  )
}

function dragCursor(
  tool: Tool,
  closed: boolean,
  hoverRing: string | null,
  rotating: string | null,
): string {
  if (rotating) return 'grabbing'
  if (hoverRing) return 'grab'
  return tool === 'draw' && !closed ? 'crosshair' : 'default'
}

/**
 * Hand the PNG to the browser.
 *
 * An anchor with `download` rather than a new tab: a 3840x2160 data URL opened
 * as a document is a page the user then has to right-click to save.
 */
function downloadPNG(url: string | null): void {
  if (!url) return
  const a = document.createElement('a')
  a.href = url
  a.download = 'room2d-topview.png'
  a.click()
}

/** Degenerate edges produce no wall, so the count is not simply the vertex count. */
function countWalls(vertices: Vertex2[]): number {
  let n = 0
  for (let i = 0; i < vertices.length; i += 1) {
    const a = vertices[i]!
    const b = vertices[(i + 1) % vertices.length]!
    if (Math.hypot(b[0] - a[0], b[1] - a[1]) >= 1e-6) n += 1
  }
  return n
}

function RectangleForm({ onCreate }: { onCreate: (w: number, d: number) => void }) {
  const { t } = useTranslation()
  const [w, setW] = useState(10)
  const [d, setD] = useState(6)
  return (
    <>
      <div className="prop-row">
        <label>{t('inspector.room_width_x')}</label>
        <NumberField value={w} step={0.5} min={0.1} onCommit={setW} />
      </div>
      <div className="prop-row">
        <label>{t('inspector.room_depth_z')}</label>
        <NumberField value={d} step={0.5} min={0.1} onCommit={setD} />
      </div>
      <button className="btn primary" id="room2d-btn-rect" onClick={() => onCreate(w, d)}>
        {t('btn.make_rectangle')}
      </button>
    </>
  )
}

/**
 * The standalone mount `window.room2d.enterEditor()` uses.
 *
 * It follows the store rather than a prop, because nothing is passing it one:
 * this tree is mounted beside App, not inside it.
 */
function Room2DStandalone({ onClose }: { onClose: () => void }) {
  const [project, setProject] = useState(() => store.getProject())
  useEffect(() => store.subscribe(() => setProject(store.getProject())), [])
  if (!project) return null
  return <Room2DEditor project={project} onClose={onClose} standalone />
}

registerMounter((onClose: () => void): ReactNode =>
  createElement(Room2DStandalone, { onClose }),
)
