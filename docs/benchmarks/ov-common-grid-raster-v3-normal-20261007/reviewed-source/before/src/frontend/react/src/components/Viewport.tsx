import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { ENVIRONMENT_INTENSITY, installRoomEnvironment } from '../renderer/environmentLighting'
import type { CfdProject } from '../schema/cfdproj'
import {
  createSceneBuilder,
  emptyRegistries,
  type SceneBuilder,
  type SceneRegistries,
} from '../interaction/sceneBuilder'
import type { PostprocessLayers } from '../postprocess/ppLayers'
import { applyPostprocessLayers, updateVolumeCamera } from '../postprocess/ppLayers'
import { createEdlPass, type EdlPass } from '../postprocess/ppEdl'
import { createTranslateGizmo, installLegacyGizmoBridge, type TranslateGizmo } from '../interaction/translateGizmo'
import { createMoveGizmo, type MoveGizmo } from '../interaction/moveGizmo'
import { createRotateGizmo, type RotateGizmoHandle } from '../interaction/rotateModelGizmo'
import { createMeshVisuals, type MeshVisuals } from '../run/meshVisuals'
import { activeSeedSurface, isSeedHelperVisible, subscribeSeedHelper } from '../geometry/seedActions'
import { buildSeedHelper, type SeedHelperHandle } from '../geometry/seedHelperVisual'
import { createSeedDragTools, type SeedDragTools } from '../geometry/seedDrag'
import { createFaceDragTools, type FaceDragTools } from '../geometry/faceDrag'
import { createAirPipeHandles, type AirPipeHandles } from '../geometry/airPipeHandles'
import { allModels } from '../schema/cfdproj'

/** The shared supply-jet animation (js/supply_flow_anim.js); this viewport drives it. */
type SupplyFlowApi = {
  rebuild: (models: unknown[], parent: unknown) => void
  tick: (now: number) => boolean
  dispose: () => void
  setVisible: (visible: boolean) => void
}
function supplyFlow(): SupplyFlowApi | null {
  const w = window as unknown as { supplyFlowAnim?: SupplyFlowApi; __supplyFlowSelfDrive?: boolean }
  w.__supplyFlowSelfDrive = false
  return w.supplyFlowAnim ?? null
}
/**
 * After a scene rebuild: the room's streams, gated on the tab — or none at all
 * in rack detail, whose scene is one rack at the origin (the streams live in
 * room coordinates and no rebuild of the builder removes them).
 */
function refreshSupplyFlow(models: unknown[], parent: unknown, activeTab: string): void {
  const flow = supplyFlow()
  if (!flow) return
  if (getRackDetail().target) {
    flow.dispose()
    return
  }
  flow.rebuild(models, parent)
  flow.setVisible(supplyFlowVisible(activeTab, null))
}
import { setDragCreateBridge } from '../interaction/dragCreate'
import { getSelection, subscribeSelection } from '../selectionBus'
import { createAnchorMarkers, selectedPatchAnchor, type AnchorMarkers } from '../geometry/anchorMarkers'
import { buildBackgroundGrid, disposeBackgroundGrid } from '../geometry/backgroundGrid'
import {
  createOrientationHelper,
  type OrientationHelper,
} from '../geometry/orientationHelperVisual'
import { isPreviewActive, subscribePreview } from '../io/stlPreview'
import { dispatchPointerDown } from '../interaction/viewportPointerChain'
import { updatePreviewNormalHelper } from '../geometry/previewNormalHelper'
import { createSensorSpheres, type SensorSpheres } from '../run/sensorSpheres'
import { installEditorKeys } from '../interaction/editorKeys'
import { createGizmoShortcutHost } from '../interaction/gizmoShortcutHost'
import { setRenderRootLookup } from '../geometry/modelOpacity'
// `applyOpacity` is the post-processing fade's material write, shared with the
// rebuild hook so the slider and the re-application cannot disagree.
import {
  applyMaterialOpacity as applyOpacity,
  createViewFadeController,
  reapplySceneFades,
  reapplyViewFades,
  type ViewFadeController,
  type ViewFades,
} from '../geometry/viewFades'
import { isUiFrozen } from '../run/session'
import { getDragAxes, setMeshVisuals } from '../run/meshViewState'
import { blockMeshBounds } from '../legacy/cfdrunBuild'
import { getRackDetail, subscribeRackDetail } from '../geometry/rackDetail'
import { supplyFlowVisible } from '../geometry/supplyFlowVisibility'
import type { TreeSelection } from '../interaction/treeNodes'
import { slotAtFacePoint } from '../geometry/rackSlots'
import { isAdditiveSelectGesture, isWholeObjectSelectGesture, rackFaceSelection } from '../interaction/rackFaceGesture'
import { requestTreeReveal } from '../interaction/treeReveal'
import { clearTransformTools } from '../interaction/deselectAll'
import {
  canSelectGeometryFromViewport,
  isSelectableHit,
  pickNearestHit,
  viewportSelectionFor,
} from '../interaction/picking'
import {
  paintSelectionHighlights,
  type EquipmentSpan,
  type HighlightSelection,
} from '../interaction/selectionHighlight'
import { equipmentList } from '../geometry/rackSelection'
import { geometryPure } from '../legacy/geometryBuilder'
import * as store from '../projectStore'
import { createTransformTools, type TransformToolsApi } from '../legacy/transformTools'
import { stateV4 } from '../legacy/cfdprojModules'
import { installFirstPerson } from '../legacy/firstPerson'
import { installLiquidScene, liquidMode, liquidPipes } from '../legacy/liquidScene'
import { isLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'
import { setPpSeedDrag } from '../postprocess/ppViewState'
import { cancelViewTransition, notifyCameraSettled, setSceneMeshes, setViewportHandle, subscribeScene } from '../viewportBus'
import { installSelectionFocus, readUnobscuredFrameArea } from '../interaction/frameSelection'
import { AxisGizmo } from './AxisGizmo'
import type { ViewOptions } from './Toolbar'

/**
 * The 3D viewport.
 *
 * Deliberately owns its Three.js objects outside React state: the scene graph is
 * mutable and per-frame, and pushing it through re-renders is how these ports go
 * wrong. React owns WHEN to rebuild (the project changed, the selection changed);
 * Three owns the objects.
 *
 * The camera is user-driven (OrbitControls), not auto-orbiting. An auto-orbit
 * looks alive in a demo but makes every screenshot a different picture, so a
 * layout comparison against the legacy app could never be conclusive.
 *
 * Y-up, matching the legacy editor (`THREE.Object3D.DEFAULT_UP.set(0, 1, 0)`);
 * the room footprint polygon is in plan coordinates, so plane.y maps to world Z.
 */

export interface ViewportProps {
  project: CfdProject | null
  selectedUid: string | null
  /** Every selected model, for the multi-select highlight. */
  selectedUids?: string[]
  view: ViewOptions
  /** `toggle` is a ctrl/cmd-click — add to the selection, do not replace it. */
  onSelect: (uid: string | null, toggle?: boolean) => void
  /** The narrower selection a viewport click can make — a rack's U-slot. */
  onSelectNode?: (selection: TreeSelection) => void
  /**
   * The app's current narrow selection, when it passes one down.
   *
   * The resize handles are drawn for whatever is selected — a patch, a face, a
   * planar model — so a patch picked in the FEATURE TREE has to reach the
   * viewport. Optional: without it the handles still follow the viewport's own
   * clicks, which is how a patch is usually selected before it is dragged.
   */
  selectedNode?: TreeSelection | null
  /** The open left tab — the mesh helpers exist only while it is `mesh`. */
  activeTab?: string
}

export function Viewport({
  project,
  selectedUid,
  selectedUids,
  view,
  onSelect,
  onSelectNode,
  selectedNode,
  activeTab = 'geometry',
}: ViewportProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null)
  const controlsRef = useRef<OrbitControls | null>(null)
  const contentRef = useRef<THREE.Group | null>(null)
  const lightsRef = useRef<THREE.Light[]>([])
  // Whether the toolbar's 💡 灯光 is on, and the hook that pushes it into the
  // post-processing shaders — they are built inside the scene effect, which the
  // toolbar effect cannot see directly.
  const lightsEnabledRef = useRef(true)
  const ppLightRef = useRef<(() => void) | null>(null)
  const gridRef = useRef<THREE.GridHelper | null>(null)
  // The real geometry builder, shared with the legacy editor. Its registries
  // outlive React renders, so they live in refs alongside the Three objects.
  const registriesRef = useRef<SceneRegistries>(emptyRegistries())
  const builderRef = useRef<SceneBuilder | null>(null)
  const globalGroupRef = useRef<THREE.Group | null>(null)
  const projectRef = useRef<CfdProject | null>(null)
  const selectedRef = useRef<string | null>(null)
  // The post-processing layers live in their own group so a result can be shown
  // and cleared without touching the model geometry — the two answer to
  // different lifecycles (a project edit vs a solve result).
  const ppGroupRef = useRef<THREE.Group | null>(null)
  // Display settings from the post-processing right panel. Held in refs, not
  // state, because every one of them is applied to Three.js objects directly —
  // and because they must be RE-APPLIED after a rebuild: a project edit throws
  // away the meshes the opacity was set on, and a re-render replaces the
  // surface the field opacity was set on. Without the refs the sliders quietly
  // snap back to full opacity the next time anything is rebuilt.
  const edlRef = useRef<EdlPass | null>(null)
  const edlEnabledRef = useRef(false)
  /**
   * The full scene refresh (rebuild + supply streams + pipes + scene bus),
   * set by the project effect once the builder exists. The fade controller
   * calls it when a fade goes back to 1: only the builders know each model's
   * own opacity.
   */
  const rebuildSceneRef = useRef<(() => void) | null>(null)
  /** The highlight pass for the current selection, set by the highlight effect. */
  const paintHighlightsRef = useRef<(() => void) | null>(null)
  /**
   * The two view fades — the mesh preview's geometry fade and
   * post-processing's 3D-model fade — and what a change of either does: a
   * fade that lands repaints the highlight, a fade that goes back to 1
   * rebuilds (`geometry/viewFades.ts`).
   */
  const fadeCtlRef = useRef<ViewFadeController | null>(null)
  fadeCtlRef.current ??= createViewFadeController({
    roots: () => registriesRef.current.renderRoots,
    scene: () => globalGroupRef.current,
    rebuild: () => rebuildSceneRef.current?.(),
    repaint: () => paintHighlightsRef.current?.(),
  })
  const fadeCtl = fadeCtlRef.current
  /**
   * Both view fades as they stand NOW — read by the scene builder after every
   * build and by the selection highlight after every repaint, so neither can
   * put a faded mesh back to solid.
   */
  const viewFadesNow = (): ViewFades => fadeCtl.now()
  const fieldOpacityRef = useRef(1)
  const lightScaleRef = useRef(1)
  /** Each light's designed intensity, so the slider scales rather than sets. */
  const lightBaseRef = useRef<number[]>([])
  /** The translate gizmo on the streamline seed sphere, when one is drawn. */
  const seedGizmoRef = useRef<TranslateGizmo | null>(null)
  /** The model move gizmo, and the shared tools it snaps and collides with. */
  const moveGizmoRef = useRef<MoveGizmo | null>(null)
  const rotateGizmoRef = useRef<RotateGizmoHandle | null>(null)
  const faceDragRef = useRef<FaceDragTools | null>(null)
  const airPipeRef = useRef<AirPipeHandles | null>(null)
  // The seed helper's live handle, so the drag tools can raycast its three
  // meshes and move them without a rebuild.
  const seedHelperRef = useRef<SeedHelperHandle | null>(null)
  const seedDragRef = useRef<SeedDragTools | null>(null)
  /**
   * The narrow selection, from whichever source last spoke.
   *
   * Three of them, because the app does not hand this down yet: the
   * `selectedNode` prop when it is passed, the selection bus when something
   * publishes to it, and this viewport's own clicks otherwise. Without any of
   * them the handles would only ever appear for a whole planar model.
   */
  const nodeRef = useRef<TreeSelection | null>(null)
  const meshVisualsRef = useRef<MeshVisuals | null>(null)
  const anchorMarkersRef = useRef<AnchorMarkers | null>(null)
  const orientationHelperRef = useRef<OrientationHelper | null>(null)
  const sensorSpheresRef = useRef<SensorSpheres | null>(null)
  const frameRef = useRef<(() => void) | null>(null)
  /** Which document the camera was framed for; see the framing note below. */
  const framedKeyRef = useRef<string | null>(null)
  // Read inside the mesh-helper callbacks, which outlive the render that made
  // them: a captured `activeTab` would freeze the helpers on the tab that was
  // open when the scene was built.
  const activeTabRef = useRef(activeTab)
  activeTabRef.current = activeTab
  // The whole selection, for the scene helpers and the keyboard handler, which
  // both outlive the render that made them: a captured array would freeze on
  // whatever was selected when the listener was installed.
  const selectedUidsRef = useRef<readonly string[]>([])
  selectedUidsRef.current = selectedUids ?? (selectedUid ? [selectedUid] : [])
  const toolsRef = useRef<TransformToolsApi | null>(null)
  // The toolbar's snap toggles, read inside the drag rather than closed over —
  // a drag can outlive the render that started it.
  const viewRef = useRef(view)
  viewRef.current = view
  const [stats, setStats] = useState({ fps: 0, meshes: 0 })
  // The click handler is installed once, so it must not close over a stale
  // callback; a ref is how it reads the current one.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onSelectNodeRef = useRef(onSelectNode)
  onSelectNodeRef.current = onSelectNode
  // The prop wins whenever the app passes one; a stale viewport-local node
  // would otherwise keep drawing handles on a patch the tree has moved off.
  if (selectedNode !== undefined) nodeRef.current = selectedNode
  // Assigned during RENDER, unlike `selectedRef`, which an effect further down
  // the file owns: the handles have to be refreshed in an effect of their own,
  // and effects run in source order, so reading that one would draw the handles
  // for the PREVIOUS selection on every change.
  const selectedNowRef = useRef<string | null>(null)
  selectedNowRef.current = selectedUid
  // The picking body, published for first-person mode. It is installed once,
  // inside the scene effect, and the effect that hands it to `fpsMode` runs
  // later — a ref is the only thing that bridges the two.
  const selectAtRef = useRef<((event: MouseEvent) => void) | null>(null)
  // The anchor a shift-click extends from.
  const lastSlotRef = useRef<{ uid: string; side: 'front' | 'back'; anchor: number } | null>(null)

  // --- one-time setup -----------------------------------------------------
  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    // alpha + no scene background: the viewport's green radial glow is painted
    // by CSS behind the canvas (see #center-view), and an opaque clear colour
    // would hide it.
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearAlpha(0)
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()

    // The legacy camera, value for value (STL_generate.html:1623): fov 45,
    // near 0.1, far 400, the south-east-above start.
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 400)
    camera.position.set(3.5, 4.5, 4.5)

    renderer.toneMapping = THREE.ACESFilmicToneMapping
    renderer.toneMappingExposure = 2.0
    renderer.outputColorSpace = THREE.SRGBColorSpace

    // The legacy OrbitControls, value for value (STL_generate.html:1640-1652):
    // no damping (the camera stops when the mouse does), the polar clamp that
    // keeps the orbit off the poles, and the button map — left is for
    // SELECTING and DRAGGING geometry and never moves the camera, middle drag
    // pans, right drag orbits (Alt+right included), the wheel dollies.
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = false
    controls.minPolarAngle = 0.001
    controls.maxPolarAngle = Math.PI - 0.001
    controls.target.set(0, 1.2, 0)
    controls.mouseButtons = {
      LEFT: null as unknown as THREE.MOUSE,
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.ROTATE,
    }
    // Free orbit (right button) leaves any axis-aligned snap: a top/bottom
    // view preset sets camera.up to ±Z to avoid gimbal lock there, and without
    // this reset that up leaks into the free rotation and the orbit pivots
    // around Z. Capture phase, so it lands before OrbitControls' own
    // pointerdown (STL_generate.html:1671-1682). Panning (middle) keeps the
    // preset's up, so top/bottom panning stays oriented.
    const resetUpOnRightButton = (e: PointerEvent) => {
      if (e.button === 2) camera.up.set(0, 1, 0)
    }
    renderer.domElement.addEventListener('pointerdown', resetUpOnRightButton, true)

    // The legacy editor's LightKit, copied exactly (STL_generate.html:1703).
    //
    // Not a stylistic choice. Three ad-hoc world lights blew the rack tops and
    // the perforated floor tiles out to flat white, so the procedural textures
    // were there in the material and invisible on screen — which reads as
    // "textures are broken" rather than "the exposure is wrong".
    //
    // Five lights, all parented to the CAMERA so the shading is view-relative
    // and a rotated model keeps its form; intensities scaled by PI because
    // r184 is physically correct; and ACES tone mapping with exposure 2.0 on
    // the renderer, which is what keeps the highlights off the clip point.
    scene.add(camera)
    const lights: THREE.Light[] = []
    const addCameraLight = (hex: number, intensity: number, x: number, y: number, z: number) => {
      const light = new THREE.DirectionalLight(hex, intensity)
      light.position.set(x, y, z)
      camera.add(light)
      camera.add(light.target)
      lights.push(light)
    }
    addCameraLight(0xfff7e6, 0.75 * Math.PI, 0.1116, 0.766, 0.633)
    addCameraLight(0xe7edff, 0.25 * Math.PI, -0.0449, -0.9659, 0.2549)
    addCameraLight(0xfefefe, 0.25 * Math.PI, 0, 0, 1)
    addCameraLight(0xfefefe, 0.2143 * Math.PI, 0.9397, 0, -0.342)
    addCameraLight(0xfefefe, 0.2143 * Math.PI, -0.9397, 0, -0.342)
    const ambient = new THREE.AmbientLight(0x606060, 0.05 * Math.PI)
    scene.add(ambient)
    lights.push(ambient)
    lightsRef.current = lights
    lightBaseRef.current = lights.map((l) => l.intensity)
    // Something for the PBR liquid equipment to reflect. Standard materials
    // only: the Phong kinds above are lit exactly as before.
    const disposeEnvironment = installRoomEnvironment(renderer, scene)

    const content = new THREE.Group()
    scene.add(content)

    rendererRef.current = renderer
    sceneRef.current = scene
    cameraRef.current = camera
    controlsRef.current = controls
    contentRef.current = content
    // Publish for the toolbar view cube and the axis triad, which live in other
    // React subtrees and need this camera's orientation.
    setViewportHandle({ camera, controls })
    // A fresh frame on demand, for the screenshot button: a WebGL canvas
    // without `preserveDrawingBuffer` is cleared after each present, so reading
    // it without drawing first gives a blank PNG.
    ;(window as unknown as { __viewportRender?: () => HTMLCanvasElement }).__viewportRender = () => {
      renderer.render(scene, camera)
      return renderer.domElement
    }

    let raf = 0
    // FPS is sampled once a second, not per frame: pushing it into React state
    // every frame would re-render the app 60 times a second to display a number.
    let frames = 0
    let lastSample = performance.now()
    const render = () => {
      raf = requestAnimationFrame(render)
      // Disabled controls must not reapply the orbit pose over first-person look.
      if (controls.enabled) controls.update()
      // The grab cubes, corner cubes and anchor balls are sized in WORLD metres
      // from the camera distance, so a zoom has to re-measure them: built once
      // at selection time, a floor picked from across the room keeps room-sized
      // cubes that bury the model when the user rolls in. Both return at once
      // when nothing is drawn, and otherwise write only on a size that changed.
      faceDragRef.current?.syncScale()
      anchorMarkersRef.current?.updateScale()
      // Every helper handed to the shared registry (gizmo handles, seed tips,
      // orientation tips, zone/AI-box centres, pipe waypoints) is re-measured
      // here against the camera. Optional-chained: a no-op until the registry
      // lands in geometry_pure.js (WP-S1).
      geometryPure().syncHelperScales?.(camera)
      // The EDL pass is built on first use and kept: it owns a full-size render
      // target with a depth texture, and rebuilding that per frame would
      // reallocate several megabytes of GPU memory sixty times a second.
      // The volume's ray starts at the camera, so its uniform is refreshed
      // before every frame rather than at build time.
      if (ppGroupRef.current) updateVolumeCamera(ppGroupRef.current, camera.position)
      supplyFlow()?.tick(performance.now())
      if (edlEnabledRef.current) {
        edlRef.current ??= createEdlPass(renderer)
        edlRef.current.render(scene, camera)
      } else {
        renderer.render(scene, camera)
      }
      frames += 1
      const now = performance.now()
      if (now - lastSample >= 1000) {
        const fps = Math.round((frames * 1000) / (now - lastSample))
        frames = 0
        lastSample = now
        setStats({ fps, meshes: registriesRef.current.renderMeshes.length })
      }
    }
    render()

    const resize = () => {
      const { clientWidth: w, clientHeight: h } = host
      if (!w || !h) return
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      // The retained camera must reach the remote view after a layout resize.
      notifyCameraSettled()
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(host)

    // --- picking ---------------------------------------------------------
    // Click, not pointerdown: OrbitControls uses drags, and selecting on
    // pointerdown would change the selection every time someone orbits. The
    // 4px slop lets a click with a shaky hand still count as a click.
    const raycaster = new THREE.Raycaster()
    const pointer = new THREE.Vector2()
    let downAt: { x: number; y: number } | null = null
    // A ring grabbed on pointerdown must not ALSO be a click that changes the
    // selection: a rotation ends where it started often enough (a 1-degree
    // nudge back and forth) that the 4px slop below would let it through.
    let rotateGrabbed = false
    // Same rule for a grabbed resize handle or a patch being dragged across its
    // face: the pointer went down ON something, so the click that follows is
    // part of that drag and must not re-select whatever ends up underneath.
    let faceGrabbed = false
    const onPointerDown = (e: PointerEvent) => {
      downAt = { x: e.clientX, y: e.clientY }
      // One order, in one place: `dispatchPointerDown`. Everything in it is
      // drawn depthTest-off on top of what it belongs to, so whoever is asked
      // first takes every press aimed at the helpers behind it — the legacy
      // dispatcher's order, valve first (STL_generate.html:18204-18211).
      const grabbed = dispatchPointerDown({
        // Liquid mode's valve → rack-manifold rubber band. It is what writes
        // `connectedRackId` / `connectedManifoldPortId`, and the graph compiler
        // only emits a rack's spoke edges once both are set
        // (js/liquid_graphgen.js:617) — so without this gesture a valve could
        // be placed and never wired.
        valveDrag: () => liquidMode()?.startValveDrag?.(e) === true,
        seedDrag: () => seedDragRef.current?.begin(e) === true,
        rotateGizmo: () => rotateGizmoRef.current?.begin(e) === true,
        pipeHandles: () => airPipeRef.current?.begin(e) === true,
        faceDrag: () => faceDragRef.current?.begin(e) === true,
      })
      rotateGrabbed = grabbed === 'rotate'
      faceGrabbed = grabbed === 'face' || grabbed === 'pipe'
      // A grabbed handle is not a click either — a nudge that ends where it
      // started would otherwise fall inside the 4px slop and re-select whatever
      // is behind the helper. The valve drag ends on its own pointerup, and
      // suppressing the click keeps that release from re-selecting the rack it
      // was dropped on.
      if (grabbed === 'seed' || grabbed === 'valve') rotateGrabbed = true
    }
    const onPointerMove = (e: PointerEvent) => {
      liquidMode()?.updateValveDrag?.(e)
      seedDragRef.current?.hover(e)
      faceDragRef.current?.hover(e)
      airPipeRef.current?.hover(e)
    }
    // The valve drag is the only gesture here that ends on the canvas' own
    // pointerup — the others own their window listeners — so it needs one.
    const onPointerUp = (e: PointerEvent) => {
      liquidMode()?.endValveDrag?.(e)
    }
    /**
     * Resolve a screen point to a selection.
     *
     * Split from the click handler so first-person mode can reach it. Its
     * crosshair click is SYNTHETIC — the module builds a MouseEvent at the
     * canvas centre and calls the hand-off directly (`_selectAtCrosshair`,
     * js/first_person_mode.js:309) — so it never went through a pointerdown,
     * and the drag-slop guard below would measure it against wherever the mouse
     * last happened to be pressed and reject most of them.
     */
    // Every node this viewport selects goes through here, so the handles know
    // what is selected even while the app is not passing `selectedNode` down.
    const emitNode = (node: TreeSelection | null) => {
      nodeRef.current = node
      if (node) onSelectNodeRef.current?.(node)
      faceDragRef.current?.refresh()
      airPipeRef.current?.refresh()
    }

    const selectAt = (e: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect()
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1
      raycaster.setFromCamera(pointer, camera)

      // Liquid handles get first refusal, and only when they are CLOSER than
      // the nearest solid mesh — a control point behind a rack must not be
      // grabbed through it. The legacy viewport does the same depth comparison
      // before offering the click to liquid mode (STL_generate.html:17620).
      const liquid = liquidMode()
      if (liquid) {
        let closest = Infinity
        const modelHits = raycaster.intersectObjects(registriesRef.current.renderMeshes, false)
        const solid = modelHits.filter((h) => !(h.object.userData as { isRackPartition?: boolean }).isRackPartition)
        if (solid.length) closest = solid[0]!.distance
        if (liquid.handleControlPointClick?.(e as PointerEvent, closest)) return
        if (liquid.handleValveClick?.(e as PointerEvent, closest)) return
      }
      // Three's raycaster tests invisible objects too, so a hidden model (an
      // unchecked wall or ceiling) would otherwise be picked through the room
      // and become the selection. Filter on visibility, then take the nearest
      // thing that is actually a model.
      //
      // `modelId`, NOT `uid`: that is the key the shared geometry builder puts
      // on every mesh it makes (`geometry_scene.js`), and looking for `uid`
      // matched nothing at all — clicking a rack in the viewport selected
      // nothing and only the tree could change the selection.
      //
      // MESHES only (`isSelectableHit`): a rack's U-slot boundary guide is a
      // THREE.Line with the rack's modelId, hit anywhere within a metre of the
      // ray, and it took front-face clicks away from the server behind it.
      const hits = raycaster.intersectObjects(content.children, true).filter(isSelectableHit)
      // Not the literally-nearest hit: a rack face stacks the panel, the slot
      // guide, the server behind it and a patch on the server within a
      // millimetre, and the one the user aimed at is the most SPECIFIC of
      // those, not whichever the renderer offset toward the camera.
      const pick = pickNearestHit(hits)
      const data = (pick?.object.userData ?? {}) as {
        modelId?: string
        uid?: string
        isBaseWall?: boolean
        faceId?: string
        isRackEquipment?: boolean
        equipmentId?: string
        isPatch?: boolean
        patchId?: string
        isRackSlot?: boolean
        slotIndex?: number
        side?: string
      }
      const uid = data.modelId ?? data.uid ?? null

      const hitKind = uid ? String(registriesRef.current.modelMap.get(uid)?.kind ?? '') : null

      // Shift promotes any visible rack, CDU or box face/patch to its whole
      // model. Unlike an additive toggle, this retains an already selected body.
      // Ctrl/Cmd still use the multi-select paths below.
      if (uid && isWholeObjectSelectGesture(e, hitKind)) {
        onSelectRef.current(uid, false)
        emitNode(null)
        return
      }

      // A slot guide: the rack's own handle on one U-slot. When the slot holds
      // a server the guide is not the target — the legacy looks PAST it for the
      // patch or face behind (STL_generate.html:17813) so a click on a specific
      // patch region reaches that patch.
      if (uid && data.isRackSlot && typeof data.slotIndex === 'number') {
        const behind = pickNearestHit(
          hits.filter((i) => !(i.object.userData as { isRackSlotGuide?: boolean }).isRackSlotGuide),
        )
        const bd = (behind?.object.userData ?? {}) as {
          isRackEquipment?: boolean
          equipmentId?: string
          faceId?: string
          isPatch?: boolean
          patchId?: string
        }
        if (bd.isRackEquipment && bd.equipmentId) {
          onSelectRef.current(uid, false)
          // Shift promotes to the whole server here too. This is the branch a
          // click through a SLOT GUIDE takes, and it is the one a user actually
          // hits on a populated rack — patching only the direct-mesh branch
          // left shift working on some pixels of a server and not others.
          emitNode(
            bd.isPatch && bd.patchId
                ? {
                    type: 'serverPatch',
                    uid,
                    equipmentId: bd.equipmentId,
                    faceId: bd.faceId,
                    patchId: bd.patchId,
                  }
                : {
                    type: 'serverFace',
                    uid,
                    equipmentId: bd.equipmentId,
                    faceId: bd.faceId ?? (data.side === 'back' ? 'outlet' : 'inlet'),
                  },
          )
          return
        }
        onSelectRef.current(uid, false)
        // Ctrl/Cmd extends the slot selection to a RANGE, which is how a 4U
        // server gets made. The legacy used shift for it
        // (STL_generate.html:17838); shift now selects the whole rack, so the
        // range gesture moved to the other modifier.
        const clicked = data.slotIndex
        const previous = lastSlotRef.current
        const extend =
          (e.ctrlKey || e.metaKey) &&
          previous &&
          previous.uid === uid &&
          previous.side === (data.side ?? 'front')
        const anchor = extend ? previous.anchor : clicked
        const indices = extend
          ? Array.from(
              { length: Math.abs(clicked - anchor) + 1 },
              (_, k) => Math.min(anchor, clicked) + k,
            )
          : [clicked]
        lastSlotRef.current = {
          uid,
          side: data.side === 'back' ? 'back' : 'front',
          anchor,
        }
        emitNode({
          type: 'rackSlot',
          uid,
          index: data.slotIndex,
          indices,
          side: data.side === 'back' ? 'back' : 'front',
        })
        return
      }

      // A server inside a rack: the click selects the SERVER, or one of its
      // patches, rather than the rack — the legacy does the same
      // (STL_generate.html:17789). Without this a click on a server showed the
      // whole cabinet's panel and there was no way to reach the server from the
      // viewport at all.
      if (uid && data.isRackEquipment && data.equipmentId) {
        onSelectRef.current(uid, false)
        // Shift promotes to the WHOLE server, exactly as it does when the same
        // server is reached through the rack's face plane
        // (`rackFaceSelection`). Without this the promotion depended on which
        // of the two the pointer happened to land on: the face plane promoted,
        // the server's own mesh did not.
        emitNode(
          data.isPatch && data.patchId
            ? {
                type: 'serverPatch',
                uid,
                equipmentId: data.equipmentId,
                faceId: data.faceId,
                patchId: data.patchId,
              }
            : data.faceId
              ? { type: 'serverFace', uid, equipmentId: data.equipmentId, faceId: data.faceId }
              : { type: 'equipment', uid, equipmentId: data.equipmentId },
        )
        return
      }

      // A rack's U-slot is resolved from the HIT POINT on the rack's own face,
      // not from a separate handle: the builder's slot guides are hover
      // overlays that the rack's blank panels sit in front of, so a
      // first-hit-wins pick never reaches them. The legacy does the same —
      // `_resolveRackFaceHitToSlot` (STL_generate.html:17570) — which is also
      // why a slot can be clicked from any angle the face is visible from.
      const side = data.faceId === 'back' ? 'back' : 'front'
      if (uid && data.isBaseWall && (data.faceId === 'front' || data.faceId === 'back')) {
        const model = registriesRef.current.modelMap.get(uid)
        const root = registriesRef.current.renderRootMap.get(uid)
        if (model?.kind === 'rack' && root && pick) {
          const layouts = geometryPure().getFaceLayouts(model) as Record<
            string,
            { origin: THREE.Vector3; right: THREE.Vector3; up: THREE.Vector3 } | undefined
          >
          const layout = layouts?.[side]
          if (layout) {
            const local = root.worldToLocal(pick.point.clone())
            const index = slotAtFacePoint(model, local, layout)
            if (index !== null) {
              onSelectRef.current(uid, false)
              // A rack face carries three different things, not one: a server
              // owns its U, a leakage zone owns its whole run, and shift
              // promotes either. `rackFaceSelection` is the legacy's own table
              // (STL_generate.html:17866-17878).
              emitNode(rackFaceSelection(model, uid, index, side, false))
              return
            }
            // The frame and safe margins fall through to ordinary face
            // selection (shift was handled above: the whole rack).
          }
        }
      }

      // Ctrl/Cmd — and shift on the other model kinds — extends the selection,
      // matching the legacy viewport (STL_generate.html
      // `handleViewportSelection`). Shift on a rack/CDU/box is the whole-model gesture
      // above.
      // A face, a patch, a wall region or an imported solid on a NON-RACK
      // model. The rack branches above resolve themselves — a U-slot needs the
      // hit POINT, which is not in the userData — but everything else IS a
      // function of it, and `viewportSelectionFor` is the legacy's own table
      // (STL_generate.html:17803-17893). Null back means "the whole model",
      // which is what a planar body click gives there too. A multi-select
      // gesture skips it, and the legacy checks that gesture first.
      if (uid && !isAdditiveSelectGesture(e, hitKind)) {
        const node = viewportSelectionFor(
          uid,
          pick?.object.userData ?? {},
          registriesRef.current.modelMap.get(uid),
        )
        if (node) {
          onSelectRef.current(uid, false)
          emitNode(node)
          return
        }
      }

      // A body click selects the whole model, so any narrower selection the
      // handles were drawn for is over.
      emitNode(null)
      // Deselecting also puts the transform tools away, as the legacy's
      // setSelection('project') does (STL_generate.html:6290-6292): leaving
      // moveTool.enabled on every model puts the drag arrows straight back on
      // the next click.
      if (!uid) clearTransformTools()
      onSelectRef.current(uid, isAdditiveSelectGesture(e, hitKind))
    }
    // Every pick made in the 3D view — a click here, or first-person mode's
    // crosshair — then brings the active model's tree row on screen
    // (`treeReveal`, the legacy `revealModelInTree`). Asked after the
    // selection changed and in the same event, so the tree acts on the new
    // selection. A click IN the tree never asks, so the tree does not scroll
    // under the pointer.
    const pickAndReveal = (e: MouseEvent) => {
      selectAt(e)
      requestTreeReveal()
    }
    selectAtRef.current = pickAndReveal

    const onClick = (e: MouseEvent) => {
      if (rotateGrabbed) {
        rotateGrabbed = false
        return
      }
      if (faceGrabbed) {
        faceGrabbed = false
        return
      }
      if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4) return
      // Off the geometry tab a click belongs to that tab's own picker (the
      // mesh and post-processing patch lists listen for themselves), never to
      // the geometry selection behind it — see `canSelectGeometryFromViewport`.
      if (!canSelectGeometryFromViewport(activeTabRef.current, isUiFrozen())) return
      pickAndReveal(e)
    }

    // Direct manipulation: the patch handles, the model edge handles, and the
    // ground plane the drag-create gesture drops a model onto.
    //
    // The scene is rebuilt WHOLE on every drag frame rather than per model —
    // this port has no `rebuildSingleModel` — but at most once per animation
    // frame, which is the budget `rebuildSingleModelForDrag` gives itself
    // (STL_generate.html:7809).
    let dragRebuildQueued = false
    /**
     * Rebuild the scene, or just the models a drag is moving.
     *
     * A drag frame used to rebuild the WHOLE room: on Shinaide (287 models,
     * 15k meshes) that is ~700 ms per frame, i.e. a 2 FPS drag. The dragged
     * models are the only ones whose geometry changed, so rebuild exactly
     * those (SceneBuilder.rebuildModels); rack-detail and STL-preview modes
     * have no per-model isolation and keep the full rebuild.
     */
    const rebuildScene = (uids?: readonly string[] | null) => {
      try {
        const builder = builderRef.current
        if (!builder) return
        const targeted = uids && uids.length > 0
        if (targeted && !getRackDetail().target && !isPreviewActive()) {
          builder.rebuildModels(uids as string[])
        } else {
          builder.rebuild()
        }
        refreshSupplyFlow(store.getProject() ? allModels(store.getProject()!).map((e) => e.model) : [], globalGroupRef.current, activeTabRef.current)
        updatePreviewNormalHelper(globalGroupRef.current)
        // The builder's hook re-faded the models it made; the supply streams
        // were rebuilt after it, and the post-processing slider fades them too.
        reapplySceneFades(registriesRef.current.renderRoots, viewFadesNow(), globalGroupRef.current)
        setSceneMeshes(registriesRef.current.renderMeshes)
        toolsRef.current?.invalidateBounds()
      } catch (err) {
        console.error('[viewport] drag rebuild failed', err)
      }
    }
    let dragRebuildUids: string[] = []
    const rebuildSoon = (uids?: readonly string[] | null) => {
      if (uids && uids.length) {
        for (const u of uids) if (!dragRebuildUids.includes(u)) dragRebuildUids.push(u)
      }
      if (dragRebuildQueued) return
      dragRebuildQueued = true
      requestAnimationFrame(() => {
        dragRebuildQueued = false
        const uidsNow = dragRebuildUids
        dragRebuildUids = []
        rebuildScene(uidsNow.length ? uidsNow : null)
      })
    }
    const syncRenderTransform = (uid: string) => {
      const root = registriesRef.current.renderRootMap.get(uid)
      const model = registriesRef.current.modelMap.get(uid)
      if (!root || !model) return
      const loc = model.geometry?.location
      root.position.set(loc?.x ?? 0, loc?.y ?? 0, loc?.z ?? 0)
      root.updateMatrixWorld(true)
    }
    faceDragRef.current = createFaceDragTools({
      scene,
      camera,
      domElement: renderer.domElement,
      orbit: controls,
      registries: () => registriesRef.current,
      tools: () => toolsRef.current,
      // The STORE's project, not the prop's: a drag commits and rebuilds
      // synchronously, before React has re-rendered this component, and the
      // prop is still the previous document then. Measured: a second drag
      // started right after the first read the patch's OLD geometry from the
      // rebuilt scene and committed it, undoing the first.
      project: () => store.getProject() ?? projectRef.current,
      // The prop when the app passes one, this viewport's own clicks otherwise.
      selection: () => ({
        uid: selectedNowRef.current,
        node:
          nodeRef.current && nodeRef.current.uid === selectedNowRef.current
            ? nodeRef.current
            : null,
      }),
      snap: () => ({ face: viewRef.current.snapFace, edge: viewRef.current.snapEdge }),
      rebuildSoon,
      rebuildNow: rebuildScene,
      // The legacy predicate: no dragging geometry while a mesh or solve has
      // the UI frozen, and none from a tab whose panel is not the editor —
      // nor off t = 0 in a transient project, where the scene is frozen.
      enabled: () => !isUiFrozen() && !store.sceneFrozen() && activeTabRef.current === 'geometry',
      // The anchor balls are sized against the handle cubes, so they are
      // re-sized whenever the cubes are rebuilt — including by a click on the
      // already-selected patch, which starts a no-move body drag instead of a
      // selection and so re-runs no React effect.
      handlesRefreshed: () => anchorMarkersRef.current?.refresh(),
    })
    // Published for browser verification, which has to reach the handles: they
    // live inside the tools' closure and there is no DOM for them.
    ;(window as unknown as { __faceDrag?: FaceDragTools | null }).__faceDrag = faceDragRef.current
    // The air-pipe waypoint balls, sharing the face handles' host contract.
    airPipeRef.current = createAirPipeHandles({
      scene,
      camera,
      domElement: renderer.domElement,
      orbit: controls,
      registries: () => registriesRef.current,
      project: () => store.getProject() ?? projectRef.current,
      selection: () => selectedNowRef.current,
      // Balls for ONE selected pipe only; a multi-selection gets none.
      selectionCount: () => selectedUidsRef.current.length,
      rebuildSoon,
      rebuildNow: rebuildScene,
      enabled: () => !isUiFrozen() && !store.sceneFrozen() && activeTabRef.current === 'geometry',
    })
    ;(window as unknown as { __airPipeHandles?: AirPipeHandles | null }).__airPipeHandles =
      airPipeRef.current

    // The drag-create gesture starts on a toolbar button and finishes here, so
    // the viewport lends it the camera, the scene and the shared snap.
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    setDragCreateBridge({
      groundPointAt: (event) => {
        const rect = renderer.domElement.getBoundingClientRect()
        pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
        pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1
        raycaster.setFromCamera(pointer, camera)
        const hit = new THREE.Vector3()
        return raycaster.ray.intersectPlane(groundPlane, hit) ? { x: hit.x, y: hit.y, z: hit.z } : null
      },
      // The handles follow the model being placed: a drag-create selects it as
      // it is born, so the overlay is built while the model is still at the
      // pointer's first position and would otherwise be left behind there.
      syncRenderTransform: (uid) => {
        syncRenderTransform(uid)
        faceDragRef.current?.refresh()
        airPipeRef.current?.refresh()
      },
      rebuildNow: (uids?: string[]) => {
        rebuildScene(uids ?? null)
        faceDragRef.current?.refresh()
        airPipeRef.current?.refresh()
      },
      snap: () => ({
        face: viewRef.current.snapFace,
        edge: viewRef.current.snapEdge,
        grid: viewRef.current.snapGrid,
      }),
      snapDelta: (uid, grid, face, edge) => {
        const tools = toolsRef.current
        if (!tools) return { x: 0, y: 0, z: 0 }
        tools.invalidateBounds(uid)
        return grid ? tools.computeGridSnap([uid]) : tools.computeProximitySnap([uid], face, edge)
      },
    })

    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    renderer.domElement.addEventListener('pointermove', onPointerMove)
    renderer.domElement.addEventListener('pointerup', onPointerUp)
    renderer.domElement.addEventListener('click', onClick)

    return () => {
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      renderer.domElement.removeEventListener('pointermove', onPointerMove)
      renderer.domElement.removeEventListener('pointerup', onPointerUp)
      renderer.domElement.removeEventListener('click', onClick)
      faceDragRef.current?.dispose()
      faceDragRef.current = null
      airPipeRef.current?.dispose()
      airPipeRef.current = null
      setDragCreateBridge(null)
      cancelAnimationFrame(raf)
      observer.disconnect()
      setViewportHandle(null)
      renderer.domElement.removeEventListener('pointerdown', resetUpOnRightButton, true)
    controls.dispose()
      edlRef.current?.dispose()
      edlRef.current = null
      seedGizmoRef.current?.dispose()
      seedGizmoRef.current = null
      disposeEnvironment()
      renderer.dispose()
      host.removeChild(renderer.domElement)
      rendererRef.current = null
    }
  }, [])

  // --- rebuild when the project changes -----------------------------------
  useEffect(() => {
    const content = contentRef.current
    const camera = cameraRef.current
    const controls = controlsRef.current
    if (!content || !camera || !controls) return

    for (const child of [...content.children]) {
      content.remove(child)
      child.traverse((o) => {
        if (o instanceof THREE.Mesh) {
          o.geometry.dispose()
          const m = o.material
          if (Array.isArray(m)) m.forEach((x) => x.dispose())
          else m.dispose()
        }
      })
    }
    projectRef.current = project
    builderRef.current?.clear()
    if (!project) return

    // Room footprint. plane is [[x, y], ...] in plan coords -> world (x, z).
    const plane = project.geometry?.plane ?? []
    let cx = 0
    let cz = 0
    let span = 12
    if (plane.length >= 3) {
      // The footprint is used for FRAMING only — it is not drawn.
      //
      // A project's floor is a real construction that the shared builder
      // renders with its own perforated-tile material. Drawing a second plane
      // here put two overlapping green surfaces in the room and hid the one
      // that actually exports.
      const xs = plane.map((p) => p[0])
      const zs = plane.map((p) => p[1])
      cx = (Math.min(...xs) + Math.max(...xs)) / 2
      cz = (Math.min(...zs) + Math.max(...zs)) / 2
      span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs), 4)
    }

    // Models: built by the SHARED geometry builder, not by box proxies here.
    //
    // The STL export reads the meshes the scene built and their exportName
    // tags, so a viewport that draws its own approximation would export a
    // different set of OpenFOAM patches from the legacy editor. `globalGroup`
    // is kept at identity and parented under `content`, which is what carries
    // the room-centring offset — the builders assume globalGroup has no
    // transform of its own.
    if (!globalGroupRef.current) {
      const g = new THREE.Group()
      globalGroupRef.current = g
    }
    const globalGroup = globalGroupRef.current
    content.add(globalGroup)

    // First-person mode reads its handles from `window.__three`, the same seam
    // the legacy page publishes. Installed here rather than at import time:
    // earlier, it would point at a renderer that is about to be replaced.
    if (rendererRef.current && sceneRef.current && cameraRef.current && controlsRef.current) {
      void installFirstPerson({
        camera: cameraRef.current,
        controls: controlsRef.current,
        renderer: rendererRef.current,
        scene: sceneRef.current,
        globalGroup,
        // The crosshair hand-off. Without it walking through the room is
        // inspect-only in the literal sense: a user standing in front of a rack
        // cannot select it, and has to leave first-person to find out which one
        // they were looking at.
        //
        // `fpsMode` captures this object once, at `enter()`
        // (js/first_person_mode.js:86), so it has to be on `window.__three`
        // BEFORE the mode is entered — which is why it is passed here rather
        // than attached afterwards.
        edit: {
          handleViewportSelection: (event) => selectAtRef.current?.(event),
          // The legacy predicate (STL_generate.html:29653): no selecting while
          // a mesh or solve has the UI frozen, and none from a tab whose panel
          // is not the geometry editor — a click there would change a selection
          // the user cannot see.
          canSelectGeometry: () => canSelectGeometryFromViewport(activeTabRef.current, isUiFrozen()),
        },
      }).catch((err) => console.error('[viewport] first-person mode failed to load', err))

      // The pipe-waypoint gizmo is built by the legacy module through the
      // page-level `createTranslateGizmo`; without it the handles never appear
      // and the click is consumed anyway.
      installLegacyGizmoBridge(
        sceneRef.current,
        cameraRef.current,
        rendererRef.current.domElement,
        controlsRef.current,
      )

      // The liquid pipe renderer and the pipe/valve interactions, from the
      // legacy modules. Loaded once the scene exists, for the same reason.
      void installLiquidScene({
        camera: cameraRef.current,
        controls: controlsRef.current,
        renderer: rendererRef.current,
        scene: sceneRef.current,
        globalGroup,
      })
        .then(() => {
          // A project may already be loaded by the time the modules land.
          liquidPipes()?.updatePipeGeometry()
          liquidMode()?.renderValves()
        })
        .catch((err) => console.error('[viewport] liquid scene failed to load', err))
    }

    if (!toolsRef.current) {
      toolsRef.current = createTransformTools({
        state: () => projectRef.current as NonNullable<typeof projectRef.current>,
        // Only `view.gridSpacing` is read; the legacy grid snap takes it from
        // the same place.
        // The toolbar holds the spacing in MILLIMETRES; the snap works in
        // metres like everything else in the project.
        uiState: () => ({ view: { gridSpacing: (viewRef.current.gridSpacingMm ?? 100) / 1000 } }),
        allModels: () => {
          const p = projectRef.current
          return p ? (stateV4().allModels(p) as never[]) : []
        },
        renderRoots: () => registriesRef.current.renderRoots,
        getRenderRootByModelId: (id) => registriesRef.current.renderRootMap.get(id) ?? null,
        isModelEnabled: (modelOrId) =>
          builderRef.current?.api.isModelEnabled(modelOrId as never) ?? true,
        getModelById: (id) => registriesRef.current.modelMap.get(id) ?? null,
        // Metres per screen pixel at the orbit target: the snap radius is a
        // fixed 10 px on screen whatever the zoom.
        snapPixelWorld: () => {
          const cam = cameraRef.current
          const el = rendererRef.current?.domElement
          if (!cam || !el) return null
          const h = el.clientHeight || 1
          const target = controlsRef.current?.target
          const dist = target ? cam.position.distanceTo(target) : 5
          return (2 * dist * Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2)) / h
        },
      })
      // Published for the panels that need world BOUNDS rather than anchors —
      // the multi-select minimum corner most of all, which is a function of the
      // built meshes and cannot be derived from the project alone.
      ;(window as unknown as { __transformTools?: unknown }).__transformTools = toolsRef.current
    }

    if (!builderRef.current) {
      builderRef.current = createSceneBuilder({
        globalGroup,
        wrapperGroup: content,
        registries: registriesRef.current,
        selectedUid: () => selectedRef.current,
        // The store's, for the same reason as the drag tools above: a rebuild
        // requested by a commit must draw the document that commit produced.
        project: () => store.getProject() ?? projectRef.current,
        rackDetail: () => {
          const d = getRackDetail()
          return { target: d.target, showContext: d.showContext }
        },
        // The textures arrive asynchronously; when they land the scene has to
        // be rebuilt for the materials to pick them up.
        onTextureLoaded: () => {
          try {
            builderRef.current?.rebuild()
        refreshSupplyFlow(store.getProject() ? allModels(store.getProject()!).map((e) => e.model) : [], globalGroupRef.current, activeTabRef.current)
            reapplySceneFades(registriesRef.current.renderRoots, viewFadesNow(), globalGroupRef.current)
            updatePreviewNormalHelper(globalGroupRef.current)
            // Texture loads replace the meshes without changing the selection
            // or sceneVersion, so repaint the current selection on those roots.
            paintHighlightsRef.current?.()
          } catch (err) {
            console.error('[viewport] rebuild after texture load failed', err)
          }
        },
        // Every build hands back fresh materials at full opacity with depth
        // writes on, so the view fades go back on whatever was just built —
        // the full room, rack detail, the STL preview, and the two racks a
        // SELECTION re-makes for their slot guides. That last one re-applied
        // only the post-processing fade, so with the mesh preview up a click
        // brought the rack back solid over the mesh patches.
        afterBuild: (roots) => reapplyViewFades(roots, viewFadesNow()),
      })
    }
    // The whole-scene refresh, one routine for the two callers: this effect
    // (the project changed) and the fade controller (a fade went back to 1,
    // and only a rebuild gives each model its own opacity back).
    const rebuildWholeScene = () => {
      builderRef.current!.rebuild()
      refreshSupplyFlow(store.getProject() ? allModels(store.getProject()!).map((e) => e.model) : [], globalGroupRef.current, activeTabRef.current)
      // A rebuild throws away the meshes the opacity sliders were applied to.
      // The builder's `afterBuild` already re-faded the models; this covers
      // the supply streams, which are rebuilt after it and which the
      // post-processing slider fades with the rest of globalGroup.
      reapplySceneFades(registriesRef.current.renderRoots, viewFadesNow(), globalGroupRef.current)
      // The tree lists wall regions that exist only in the built meshes, so it
      // has to be told when they change. The bump also re-runs the highlight
      // effect, which repaints the new meshes.
      setSceneMeshes(registriesRef.current.renderMeshes)
      // Every mesh was just rebuilt, so every cached bounding box is stale.
      toolsRef.current?.invalidateBounds()
      // Pipes live in their own group under globalGroup and are NOT built by
      // the geometry builder, so a rebuild drops them; the renderer puts them
      // back from the project.
      liquidPipes()?.updatePipeGeometry()
      // And the VALVES, which are their own meshes at their own world
      // positions. Only `redrawPipes` (a drag) was drawing them, so on a fresh
      // load a project with 16 valves had none on screen — measured — and with
      // no valve mesh there is nothing to click, nothing to rotate, and nothing
      // to drag to a manifold.
      liquidMode()?.renderValves()
      // Same reason: the rebuild threw away the meshes the normal arrow was
      // parented to, so it has to be put back on whatever is selected now.
      updatePreviewNormalHelper(globalGroupRef.current)
    }
    rebuildSceneRef.current = () => {
      try {
        rebuildWholeScene()
      } catch (err) {
        console.error('[viewport] rebuild after a view fade ended failed', err)
      }
    }
    try {
      rebuildWholeScene()
    } catch (err) {
      // A builder failure must be visible. Silently catching it leaves an empty
      // viewport that looks like "the project has no geometry".
      console.error('[viewport] geometry build failed', err)
      ;(window as unknown as { __viewportError?: unknown }).__viewportError = err
    }
    ;(window as unknown as { __viewportRegistries?: unknown }).__viewportRegistries =
      registriesRef.current
    // The scene builder, exposed for the parity harness. STL export is a
    // function of the BUILT scene, so the only way to compare the two apps'
    // exports is to drive this one from outside.
    ;(window as unknown as { __sceneBuilder?: unknown }).__sceneBuilder = builderRef.current

    // The post-processing group sits beside the model geometry under `content`,
    // so it inherits nothing from the builders and survives their rebuilds.
    if (!ppGroupRef.current) {
      const g = new THREE.Group()
      g.name = 'postprocess'
      ppGroupRef.current = g
    }
    content.add(ppGroupRef.current)

    // Frame the room by MOVING THE CAMERA, never the content.
    //
    // `content` used to be offset by -(cx, cz) to put the room on the origin.
    // That offset landed in the world matrix, and the STL exporter writes WORLD
    // coordinates — so every exported vertex was shifted by the room centre and
    // the case would have meshed at the wrong place. The byte oracle caught it:
    // same solid names, same facet count, every vertex moved by exactly
    // (2.2, 3.0) on Base_leakage, which is its centre.
    content.position.set(0, 0, 0)
    const height = project.geometry?.height ?? 3
    // Kept as a callable so leaving rack detail mode can re-frame the room
    // without re-running the whole rebuild effect.
    frameRef.current = () => {
      camera.position.set(cx + span * 0.9, Math.max(height * 2, span * 0.6), cz + span * 1.1)
      controls.target.set(cx, height / 2, cz)
      controls.update()
    }
    // Framed on a LOAD, not on every edit.
    //
    // This effect re-runs for EVERY project change — a rename, a checkbox, the
    // commit at the end of an opacity drag — and framing there threw the user's
    // camera back to the default view each time, measured at (3.3, 2.1, 3.3) ->
    // (7.6, 7.2, 9.6) from a single checkbox. The document identity is what
    // decides: a different file gets framed, an edit to the open one does not.
    const framedKey = `${project.uid ?? ''}|${store.getPath() ?? ''}`
    if (framedKeyRef.current !== framedKey) {
      framedKeyRef.current = framedKey
      frameRef.current()
    }
  }, [project])

  // Display-mode detail belongs to the already loaded structural roots. A
  // user normally loads in air mode and switches later; rebuilding only pipes
  // left the CDU/rack roots in their air appearance until the next project
  // edit. SceneBuilder owns the mode subscription and returns its cleanup so
  // StrictMode/remounts cannot accumulate listeners.
  useEffect(() => {
    const builder = builderRef.current
    if (!builder) return
    return builder.watchLiquidMode(() => {
      try {
        const globalGroup = globalGroupRef.current
        setSceneMeshes(registriesRef.current.renderMeshes)
        toolsRef.current?.invalidateBounds()
        liquidPipes()?.updatePipeGeometry()
        liquidMode()?.renderValves()
        refreshSupplyFlow(
          store.getProject() ? allModels(store.getProject()!).map((entry) => entry.model) : [],
          globalGroup,
          activeTabRef.current,
        )
        reapplySceneFades(registriesRef.current.renderRoots, viewFadesNow(), globalGroup)
        updatePreviewNormalHelper(globalGroup)
      } catch (err) {
        console.error('[viewport] liquid display-mode rebuild failed', err)
      }
    })
  }, [project])

  // --- post-processing layers ---------------------------------------------
  //
  // Exposed imperatively rather than as a prop: the payloads are megabytes of
  // typed arrays that change on a button press, and routing them through React
  // state would re-render the whole panel tree to hand Three.js the same
  // buffers it already has.
  useEffect(() => {
    const applyFieldOpacity = (value: number) => applyOpacity(ppGroupRef.current, value, 'surface')

    /**
     * Put the translate gizmo on the seed sphere, or take it away.
     *
     * The sphere is rebuilt on every apply, so the gizmo is re-attached rather
     * than created once. Its drag end reports the new centre through the shared
     * store — the X/Y/Z boxes are in the panel, a different React subtree.
     *
     * The commit reads the object the GIZMO reports, never one captured here:
     * this closure runs once per apply and the sphere it found is disposed by
     * the next one, so a captured reference committed a dead object's position
     * — measured as a seed that moved to y=-1.0175 under the cursor and snapped
     * back to 0.7103 on release, with the X/Y/Z boxes never moving at all.
     */
    const attachSeedGizmo = () => {
      let seed: THREE.Object3D | null = null
      ppGroupRef.current?.traverse((o) => {
        if ((o.userData as { ppLayer?: string }).ppLayer === 'seed') seed = o
      })
      if (!seed) {
        seedGizmoRef.current?.dispose()
        seedGizmoRef.current = null
        return
      }
      const scene = sceneRef.current
      const camera = cameraRef.current
      const renderer = rendererRef.current
      if (!scene || !camera || !renderer) return
      if (!seedGizmoRef.current) {
        seedGizmoRef.current = createTranslateGizmo(scene, camera, renderer.domElement, seed, {
          size: 0.8,
          orbit: controlsRef.current,
          onDragEnd: (target) => {
            if (!target) return
            setPpSeedDrag([target.position.x, target.position.y, target.position.z])
          },
        })
      } else {
        seedGizmoRef.current.attach(seed)
      }
    }

    const applyLightScale = (value: number) => {
      lightsRef.current.forEach((l, i) => {
        l.intensity = (lightBaseRef.current[i] ?? l.intensity) * value
      })
      if (sceneRef.current?.environment) sceneRef.current.environmentIntensity = ENVIRONMENT_INTENSITY * value
      applyPpLightIntensity()
    }

    /**
     * Push the light state into the post-processing shaders.
     *
     * The result layers are drawn with a ShaderMaterial that does its OWN
     * lighting through `uLightIntensity` (ppMaterials.ts:42) — hiding the
     * scene's THREE.Light objects does nothing to them. So with a result
     * loaded, 💡 灯光 dimmed the room and left the field fully lit, which is
     * the one combination that makes the colours unreadable. The legacy zeroes
     * the same uniform on the surface, every slice and every streamline tube
     * (`toggleSceneLights`, STL_generate.html:13378).
     *
     * The VOLUME is the exception, and it needs naming: its ray-marcher has no
     * `uLightIntensity` — it scales the marched colour by `uBrightness`
     * (`ppVolume.ts:194`). So the traversal skipped it, and the slider left the
     * one layer that is pure colour at full brightness while everything around
     * it dimmed. `ppSetLightIntensity` writes both in the legacy too
     * (STL_generate.html:22304).
     */
    const applyPpLightIntensity = () => {
      const value = lightsEnabledRef.current ? lightScaleRef.current : 0
      ppGroupRef.current?.traverse((o) => {
        const material = (o as THREE.Mesh).material as
          | (THREE.Material & { uniforms?: Record<string, { value: unknown }> })
          | undefined
        const uniform = material?.uniforms?.uLightIntensity
        if (uniform) uniform.value = value
        const brightness = material?.uniforms?.uBrightness
        if (brightness) brightness.value = value
      })
    }

    // Published so the toolbar's lights toggle can reach the pp shaders, which
    // are built in here.
    ppLightRef.current = applyPpLightIntensity

    const api = {
      apply(layers: PostprocessLayers) {
        const g = ppGroupRef.current
        if (!g) return
        applyPostprocessLayers(g, layers)
        // Freshly built materials start at full intensity; if the lights are
        // off they must not.
        applyPpLightIntensity()
        // The layers were just rebuilt from scratch, so the field-opacity
        // slider's value is not on them yet.
        applyFieldOpacity(fieldOpacityRef.current)
        // The gizmo held the PREVIOUS seed sphere, which has just been disposed.
        // Re-attaching to the new one is what keeps the handles usable across a
        // radius change; without it the gizmo points at a freed object and the
        // next drag moves nothing.
        attachSeedGizmo()
      },
      clear() {
        seedGizmoRef.current?.dispose()
        seedGizmoRef.current = null
        const g = ppGroupRef.current
        if (g) applyPostprocessLayers(g, { surface: { visible: false, colorMap: 'coolwarm', climMin: 0, climMax: 0, autoScale: true } })
      },
      /**
       * What is currently drawn, for the verification harness to read back.
       *
       * Reports DRAWN indices per layer as well as vertices. Hiding a patch
       * drops its triangles from the index buffer while leaving the shared
       * point array intact — so a vertex count alone cannot tell a hidden patch
       * from a visible one, and a harness watching only vertices would report
       * the visibility checkboxes working when nothing had changed.
       */
      stats() {
        const g = ppGroupRef.current
        const layers: Record<string, number> = {}
        const indices: Record<string, number> = {}
        let vertices = 0
        g?.traverse((o) => {
          const layer = (o.userData as { ppLayer?: string }).ppLayer
          if (!layer) return
          layers[layer] = (layers[layer] ?? 0) + 1
          // Sum over the SUBTREE, not the tagged object alone. Streamlines are
          // a group of one tube per polyline and carry no geometry themselves,
          // so counting only the tagged object reports them as present and
          // empty — which is indistinguishable from a set that failed to loft.
          o.traverse((child) => {
            const mesh = child as THREE.Mesh
            const pos = mesh.geometry?.getAttribute('position')
            if (pos) vertices += pos.count
            const index = mesh.geometry?.getIndex()
            if (index) indices[layer] = (indices[layer] ?? 0) + index.count
          })
        })
        return { layers, indices, vertices }
      },

      /** 3D模型透明度 — see the legacy `ppSetModelOpacity`. */
      setModelOpacity(value: number) {
        // Through the fade controller: a fade repaints the highlight over it,
        // and going back to 1 rebuilds rather than writing 1 over every
        // model's own opacity.
        fadeCtl.setModel(value)
      },
      /** 表面场变量透明度 — the coloured boundary surface only. */
      setFieldOpacity(value: number) {
        fieldOpacityRef.current = value
        applyFieldOpacity(value)
      },
      /**
       * 渲染场光照强度.
       *
       * The legacy editor pushes this into a `uLightIntensity` uniform on its
       * own surface / slice / streamline shaders. This port draws those layers
       * with stock Three materials, so the equivalent is to scale the LightKit
       * — same meaning for the lit layers, and no effect on the slice and the
       * streamlines, which are unlit here and were shader-lit there.
       */
      setLightIntensity(value: number) {
        lightScaleRef.current = value
        applyLightScale(value)
      },
      /** 深度增强 (AO) on/off. Returns the state, like `ppToggleAO`. */
      setAO(on: boolean) {
        edlEnabledRef.current = on
        return edlEnabledRef.current
      },
      setAOStrength(value: number) {
        edlRef.current ??= rendererRef.current ? createEdlPass(rendererRef.current) : null
        edlRef.current?.setStrength(value)
      },
      /**
       * internalMesh — the layers that are not boundary patches.
       *
       * The legacy checkbox toggles its slice and streamline GROUPS. Here those
       * layers are individual objects tagged in userData, so the tags are what
       * gets toggled. The seed sphere is deliberately left alone: it is a
       * control for where streamlines come from, not part of the result, and
       * the legacy editor keeps it outside the group this checkbox hides.
       */
      setInternalVisible(visible: boolean) {
        const g = ppGroupRef.current
        if (!g) return
        g.traverse((o) => {
          const layer = (o.userData as { ppLayer?: string }).ppLayer
          if (layer === 'slice' || layer === 'sliceRing' || layer === 'streamlines') {
            o.visible = visible
          }
        })
      },
      /**
       * Which layer draws on top, from the render-order chips.
       *
       * The list is top-of-panel first, and the FIRST chip must win, so the
       * order is inverted into `renderOrder` values spaced by 50 — the legacy
       * `_roBaseOrder`, `(n - 1 - i) * 50`. The spacing leaves room for a layer
       * to offset itself within its own band (mesh-preview wireframes sit one
       * above their faces) without crossing into the next layer.
       */
      setRenderOrder(keys: string[]) {
        const n = keys.length
        keys.forEach((key, i) => {
          const ro = (n - 1 - i) * 50
          if (key === 'geometry') {
            globalGroupRef.current?.traverse((o) => {
              if ((o as THREE.Mesh).isMesh || (o as THREE.LineSegments).isLineSegments) {
                o.renderOrder = ro
              }
            })
            return
          }
          // 'meshPreview' has no group in this app yet — the mesh-preview
          // viewer is not migrated. The chip is kept so the panel matches the
          // legacy one, and this is a no-op until that layer exists.
          const layers =
            key === 'surfaceField'
              ? ['surface']
              : key === 'slice'
                ? ['slice', 'sliceRing']
                : key === 'streamline'
                  ? ['streamlines', 'seed']
                  : []
          if (!layers.length) return
          ppGroupRef.current?.traverse((o) => {
            const layer = (o.userData as { ppLayer?: string }).ppLayer
            if (layer && layers.includes(layer)) o.renderOrder = ro
          })
        })
      },

      /**
       * The seed gizmo's state, for the verification harness.
       *
       * A gizmo is only useful if it is ATTACHED to the object on screen; a
       * detached one still draws its arrows and moves nothing, which looks
       * identical in a screenshot.
       */
      seedGizmo() {
        const g = seedGizmoRef.current
        if (!g) return null
        const target = g.controls.object
        return {
          attached: !!target,
          visible: g.helper.visible,
          position: target ? [target.position.x, target.position.y, target.position.z] : null,
        }
      },

      /** The current display settings, for the verification harness. */
      display() {
        return {
          modelOpacity: fadeCtl.now().model,
          fieldOpacity: fieldOpacityRef.current,
          lightScale: lightScaleRef.current,
          ao: edlEnabledRef.current,
          aoStrength: edlRef.current?.strength() ?? null,
        }
      },
    }
    ;(window as unknown as { __ppViewport?: unknown }).__ppViewport = api
    return () => {
      delete (window as unknown as { __ppViewport?: unknown }).__ppViewport
    }
  }, [])

  // --- liquid: control points follow the selection -------------------------
  //
  // Selecting a pipe is what makes its waypoint handles appear; the legacy
  // editor calls this from its own selection path (STL_generate.html:12700).
  useEffect(() => {
    liquidMode()?.onPipeSelectionChanged(
      selectedUid ? ({ type: 'model', modelId: selectedUid } as never) : null,
    )
  }, [selectedUid, project])

  // --- liquid: the T-branch rotate gizmo -----------------------------------
  //
  // Selecting a `pipe_junction` in liquid mode attaches a Z-only gizmo to its
  // cube; anything else detaches it. The legacy does this from the same
  // selection path (STL_generate.html:6397-6408).
  //
  // The gizmo itself was already here — `js/liquid_mode.js:261` ships with the
  // shared modules this app loads, and every global it reads (`camera`,
  // `renderer`, `scene`, `controls`, `globalGroup`, `_modelMap`,
  // `pushUndoCheckpoint`, `getSelectedModel`) is installed by `liquidScene`.
  // Nothing called it, so a T-branch could be created, connected and moved, and
  // never turned: its three ports stayed pointing wherever they were born, and
  // the only way to route a branch the other way was to delete it and place
  // another.
  useEffect(() => {
    const mode = liquidMode()
    if (!mode) return
    const sync = () => {
      // Read from the PROJECT, not `registriesRef.modelMap`. That map is the
      // scene BUILDER's registry, and a junction is not built by the builder —
      // the shared pipe renderer draws its cube into `globalGroup` on its own.
      // So the map has no entry for one, and a modelMap lookup here silently
      // detached the gizmo for every junction ever selected.
      const project = projectRef.current
      const model =
        project && selectedUid ? (store.findConstruction(project, selectedUid)?.model ?? null) : null
      if (isLiquidModeActive() && model?.kind === 'pipe_junction' && !store.sceneFrozen()) {
        mode.attachJunctionGizmo(selectedUid!)
      } else {
        mode.detachJunctionGizmo()
      }
    }
    sync()
    // Also on the mode switch itself: leaving liquid mode with a junction
    // selected has to take the gizmo away, and coming back has to bring it.
    const stop = subscribeLiquidMode(sync)
    // Detached on unmount as well: the gizmo holds the junction's cube, and a
    // rebuild that disposes the cube would leave TransformControls attached to
    // a mesh no longer in the scene.
    return () => {
      stop()
      mode.detachJunctionGizmo()
    }
  }, [selectedUid, project])

  // --- the model move gizmo ------------------------------------------------
  //
  // Rebuilt when the selection changes and re-centred when the project does.
  // Never torn down mid-drag: TransformControls owns the pivot then, and
  // disposing under it leaves the camera frozen with nothing to release it.
  useEffect(() => {
    const scene = sceneRef.current
    const camera = cameraRef.current
    const renderer = rendererRef.current
    const globalGroup = globalGroupRef.current
    const tools = toolsRef.current
    if (!scene || !camera || !renderer || !globalGroup || !tools) return

    const model = selectedUid ? (registriesRef.current.modelMap.get(selectedUid) ?? null) : null
    // Off t = 0 in a transient project the scene is frozen: no gizmo to offer.
    const enabled =
      !!model &&
      (model as unknown as { moveTool?: { enabled?: boolean } }).moveTool?.enabled === true &&
      !store.sceneFrozen()

    if (moveGizmoRef.current?.dragging()) return
    moveGizmoRef.current?.dispose()
    moveGizmoRef.current = null
    if (!enabled || !model) return

    moveGizmoRef.current = createMoveGizmo({
      scene,
      camera,
      domElement: renderer.domElement,
      globalGroup,
      orbit: controlsRef.current,
      tools,
      model: () => (selectedUid ? (registriesRef.current.modelMap.get(selectedUid) ?? null) : null),
      // Multi-move: every OTHER selected model whose own move tool is on,
      // translated by the same delta.
      others: () =>
        (selectedUids ?? [])
          .filter((uid) => uid !== selectedUid)
          .map((uid) => registriesRef.current.modelMap.get(uid))
          .filter((m): m is NonNullable<typeof m> => !!m)
          .filter(
            (m) =>
              (m as unknown as { moveTool?: { enabled?: boolean } }).moveTool?.enabled === true,
          ),
      // The rendered group follows the anchor DIRECTLY during a drag. Going
      // through a project rebuild would re-create every mesh in the room sixty
      // times a second.
      syncRenderTransform: (uid) => {
        const root = registriesRef.current.renderRootMap.get(uid)
        const m = registriesRef.current.modelMap.get(uid)
        if (!root || !m) return
        const loc = m.geometry?.location
        root.position.set(loc?.x ?? 0, loc?.y ?? 0, loc?.z ?? 0)
        root.updateMatrixWorld(true)
      },
      snap: () => ({
        face: viewRef.current.snapFace,
        edge: viewRef.current.snapEdge,
        grid: viewRef.current.snapGrid,
      }),
    })
    // For the parity harness: how many models the gizmo will move.
    ;(window as unknown as { __moveGizmoGroupSize?: () => number }).__moveGizmoGroupSize = () =>
      moveGizmoRef.current?.groupSize() ?? 0
    return () => {
      if (moveGizmoRef.current?.dragging()) return
      moveGizmoRef.current?.dispose()
      moveGizmoRef.current = null
    }
    // `selectedUids` is in here because the gizmo's `others()` closes over it:
    // without it a model ctrl-clicked into the selection AFTER the gizmo
    // appeared would not move with the group.
  }, [selectedUid, selectedUids, project])

  // --- the seed helper, and dragging it -------------------------------------
  //
  // The hub and its two arrows for the seed surface the inspector is showing,
  // plus the pointer handling that makes them grabbable. Without the helper the
  // panel's 显示 toggle wrote a flag nothing read; without the drag the helper
  // was decoration, and the seed ANCHOR — the one field that puts a seed point
  // out of solid geometry — had no control anywhere in the app.
  useEffect(() => {
    const scene = sceneRef.current
    const globalGroup = globalGroupRef.current
    const camera = cameraRef.current
    const renderer = rendererRef.current
    if (!scene || !globalGroup || !camera || !renderer) return

    const redraw = () => {
      seedHelperRef.current?.dispose()
      seedHelperRef.current = null
      const surface = activeSeedSurface()
      if (!surface || !isSeedHelperVisible()) {
        renderer.render(scene, camera)
        return
      }
      const handle = buildSeedHelper(surface)
      seedHelperRef.current = handle
      globalGroup.add(handle.group)
      renderer.render(scene, camera)
    }
    redraw()
    const stop = subscribeSeedHelper(redraw)

    seedDragRef.current = createSeedDragTools({
      camera,
      domElement: renderer.domElement,
      orbit: controlsRef.current,
      surface: () => activeSeedSurface(),
      handles: () => seedHelperRef.current?.handles() ?? [],
      reposition: (cfg) => seedHelperRef.current?.reposition(cfg),
      render: () => renderer.render(scene, camera),
      // The legacy predicate, field for field (STL_generate.html:18034): the
      // geometry tab, the helper shown, and nothing frozen by a running job.
      enabled: () =>
        !isUiFrozen() && activeTabRef.current === 'geometry' && isSeedHelperVisible(),
    })
    // Published for browser verification: the handles live inside the helper's
    // closure and have no DOM of their own, so a scripted drag has to reach the
    // tools directly — the same reason `__faceDrag` is exposed.
    ;(window as unknown as { __seedDrag?: SeedDragTools | null }).__seedDrag = seedDragRef.current

    return () => {
      stop()
      seedDragRef.current?.dispose()
      seedDragRef.current = null
      seedHelperRef.current?.dispose()
      seedHelperRef.current = null
    }
  }, [project])

  // --- the model rotate gizmo ----------------------------------------------
  //
  // Rings, not TransformControls: the legacy gizmo is three tori with a handle
  // each, and 旋转吸附 in the toolbar quantises the DRAG — a flag that had
  // nothing to read it until this existed.
  useEffect(() => {
    const camera = cameraRef.current
    const renderer = rendererRef.current
    const globalGroup = globalGroupRef.current
    const tools = toolsRef.current
    if (!camera || !renderer || !globalGroup || !tools) return

    const chosen = () => {
      if (store.sceneFrozen()) return []
      const uids = selectedUids?.length ? selectedUids : selectedUid ? [selectedUid] : []
      return uids
        .map((uid) => registriesRef.current.modelMap.get(uid))
        .filter((m): m is NonNullable<typeof m> => !!m)
        .filter(
          (m) => (m as unknown as { rotateTool?: { enabled?: boolean } }).rotateTool?.enabled === true,
        )
    }
    if (rotateGizmoRef.current?.dragging()) return
    rotateGizmoRef.current?.dispose()
    rotateGizmoRef.current = null
    if (!chosen().length) return

    rotateGizmoRef.current = createRotateGizmo({
      parent: globalGroup,
      camera,
      domElement: renderer.domElement,
      orbit: controlsRef.current,
      models: chosen,
      // Position AND orientation: a rotation that only moved the anchor would
      // slide the rack sideways without turning it.
      syncRenderTransform: (uid) => {
        const root = registriesRef.current.renderRootMap.get(uid)
        const m = registriesRef.current.modelMap.get(uid)
        if (!root || !m) return
        const loc = m.geometry?.location
        root.position.set(loc?.x ?? 0, loc?.y ?? 0, loc?.z ?? 0)
        geometryPure().applyModelTransformToObject(root, m)
        root.updateMatrixWorld(true)
      },
      degreeSnap: () => viewRef.current.rotationSnap === true,
      requestRender: () => {
        renderer.render(sceneRef.current!, camera)
      },
      invalidateBounds: (uid) => tools.invalidateBounds(uid),
    })
    // Published for browser verification: a synthesised drag has to know where
    // the rings are, and they live inside the gizmo's closure.
    ;(window as unknown as { __rotateGizmo?: RotateGizmoHandle | null }).__rotateGizmo =
      rotateGizmoRef.current
    return () => {
      if (rotateGizmoRef.current?.dragging()) return
      rotateGizmoRef.current?.dispose()
      rotateGizmoRef.current = null
      ;(window as unknown as { __rotateGizmo?: unknown }).__rotateGizmo = null
    }
  }, [selectedUid, selectedUids, project])

  // The selection bus, for the narrow selection made OUTSIDE this viewport —
  // the feature tree's rows. It carries a node only once something publishes to
  // it; until then the handles follow this viewport's own clicks.
  useEffect(
    () =>
      subscribeSelection(() => {
        nodeRef.current = getSelection().node
        faceDragRef.current?.refresh()
        airPipeRef.current?.refresh()
      }),
    [],
  )

  // --- the patch and model resize handles ----------------------------------
  //
  // Rebuilt whenever the selection or the drawn geometry changes: the handles
  // are world-space lines and cubes, so a project edit that moved the face they
  // sit on would otherwise leave them behind, floating where it used to be.
  //
  // `selectedUids` too: a ctrl-click that adds a second model leaves the active
  // uid on the pipe, and the pipe's waypoint balls have to go (see
  // `AirPipeHandlesHost.selectionCount`).
  useEffect(() => {
    faceDragRef.current?.refresh()
    airPipeRef.current?.refresh()
  }, [project, selectedUid, selectedUids, selectedNode, activeTab])

  // --- mesh preview ---------------------------------------------------------
  // Published on `window` rather than passed down: the panel that owns the
  // preview lives in the right column, six components away from this scene, and
  // the payload is a Three.js group — not something to route through props.
  useEffect(() => {
    const w = window as unknown as {
      __attachMeshPreview?: (g: unknown) => void
      __setGeometryOpacity?: (v: number) => void
    }
    w.__attachMeshPreview = (group: unknown) => {
      const content = contentRef.current
      if (!content) return
      // Remove whatever preview is already mounted before adding the next, so
      // a reload cannot leave two meshes blended over each other.
      const existing = content.children.find((c) => c.name === 'meshPreview')
      if (existing) content.remove(existing)
      if (group) content.add(group as THREE.Object3D)
      ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
    }
    w.__setGeometryOpacity = (v: number) => {
      // The MODEL geometry only — the preview group carries its own opacity,
      // and fading it here would fade the mesh along with what it is drawn over.
      // Through the same fade the opacity slider uses (`applyOpacityToMeshes`):
      // it owns the materials before writing, keeps the edge curve (a wall at
      // 30% with full-strength edges reads as a black grid), skips the seed
      // and anchor helpers, and carries the draw-call fixes the fade needs.
      // The legacy's mpSetGeoOpacity goes through _applyOpacityToMeshes too.
      // The controller also repaints the highlight over the fade, and on the
      // way back to 1 (preview hidden or closed, a tab switch) rebuilds the
      // room instead of writing 1 — the legacy's mpClose -> doRebuildGeometry.
      fadeCtl.setPreview(v)
      ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
    }
    return () => {
      delete w.__attachMeshPreview
      delete w.__setGeometryOpacity
    }
  }, [])

  // STL preview: the yellow outward-normal arrow follows the selected region.
  // Subscribed rather than rebuilt with the scene, because picking a different
  // region changes nothing the geometry builder knows about
  // (`updateStlPreviewNormalHelper` is called from the legacy's
  // `updateHighlights` for the same reason, STL_generate.html:6683).
  useEffect(
    () => subscribePreview(() => updatePreviewNormalHelper(globalGroupRef.current)),
    [],
  )

  // --- supply streams follow the tab ----------------------------------------
  // Hidden off the geometry tab without a rebuild (the rebuilds above gate them
  // too, for the tab they happen on).
  useEffect(() => {
    supplyFlow()?.setVisible(supplyFlowVisible(activeTab, getRackDetail().target))
    ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
  }, [activeTab])

  // --- rack detail mode ----------------------------------------------------
  // Entering swaps the whole scene for one rack, so the camera has to be
  // re-framed: the saved room view looks at where the rack USED to be, and the
  // rack is now at the origin.
  useEffect(
    () =>
      subscribeRackDetail(() => {
        const detail = getRackDetail()
        try {
          builderRef.current?.rebuild()
        refreshSupplyFlow(store.getProject() ? allModels(store.getProject()!).map((e) => e.model) : [], globalGroupRef.current, activeTabRef.current)
        } catch (err) {
          console.error('[viewport] rack detail rebuild failed', err)
        }
        setSceneMeshes(registriesRef.current.renderMeshes)
        // The liquid layer is drawn by the shared modules, not the builder:
        // in detail mode they clear and stop, on exit they redraw. The legacy
        // calls both after every detail rebuild (STL_generate.html:7594).
        liquidPipes()?.updatePipeGeometry()
        liquidMode()?.renderValves()
        const camera = cameraRef.current
        const controls = controlsRef.current
        if (detail.target && camera && controls) {
          const root = registriesRef.current.renderRootMap.get(detail.target)
          if (root) {
            const box = new THREE.Box3().setFromObject(root)
            const centre = box.getCenter(new THREE.Vector3())
            const size = box.getSize(new THREE.Vector3())
            // Looking at the INLET face (-Z), which is the face the U-slot
            // layout is authored against.
            const dist = Math.max(size.x, size.y, size.z) * 2.2
            camera.position.set(centre.x, centre.y, centre.z - dist)
            controls.target.copy(centre)
            controls.update()
          }
        } else if (camera && controls) {
          frameRef.current?.()
        }
        ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
      }),
    [],
  )

  // --- mesh-tab helpers ----------------------------------------------------
  // Created once the scene exists and kept for the session: the seed sphere and
  // the zone boxes must survive a geometry rebuild, so they hang off
  // globalGroup rather than the content the builder replaces.
  useEffect(() => {
    const camera = cameraRef.current
    const renderer = rendererRef.current
    const globalGroup = globalGroupRef.current
    if (!camera || !renderer || !globalGroup) return
    if (meshVisualsRef.current) return
    const visuals = createMeshVisuals({
      camera,
      domElement: renderer.domElement,
      globalGroup,
      orbit: controlsRef.current,
      project: () => projectRef.current,
      bounds: () => {
        const proj = projectRef.current
        const g = globalGroupRef.current
        return proj && g ? blockMeshBounds(proj, g) : null
      },
      activeTab: () => activeTabRef.current,
      dragAxes: () => getDragAxes(),
      requestRender: () => {
        ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
      },
      // A drag mutates the project in place, so React has no idea anything
      // changed; this is what moves the panel's number boxes with the sphere.
      onChange: () => store.notifyLiveEdit(),
    })
    meshVisualsRef.current = visuals
    setMeshVisuals(visuals)
    visuals.refresh()
    return () => {
      setMeshVisuals(null)
      meshVisualsRef.current?.dispose()
      meshVisualsRef.current = null
    }
  }, [])

  // Re-read after any project edit or tab switch — new zone, moved seed, a
  // different base size under a shown grid.
  useEffect(() => {
    meshVisualsRef.current?.refresh()
  }, [project, activeTab])

  // --- the per-model opacity slider's live preview -------------------------
  //
  // The slider is in the right panel and the meshes it fades are in here. It
  // reaches them through this lookup rather than a prop: the two are separate
  // React subtrees, and the preview has to run without a re-render at all.
  useEffect(() => {
    setRenderRootLookup((uid) => registriesRef.current.renderRootMap.get(uid) ?? null)
    return () => setRenderRootLookup(null)
  }, [])

  // --- anchor markers ------------------------------------------------------
  //
  // Bumped by the scene bus on every rebuild (declared here, ahead of its first
  // reader: the anchor markers below re-place a dragged patch's ball from it,
  // and the highlight effect further down re-paints the new meshes from it).
  const [sceneVersion, setSceneVersion] = useState(0)
  useEffect(() => subscribeScene((v) => setSceneVersion(v)), [])

  // Created against `globalGroup` on the first render that has one — NOT on
  // mount, where the project is still loading and there is no group to hang
  // them off yet.
  useEffect(() => {
    const globalGroup = globalGroupRef.current
    if (!globalGroup) return
    if (!anchorMarkersRef.current) {
      anchorMarkersRef.current = createAnchorMarkers({
        globalGroup,
        project: () => projectRef.current,
        selectedUids: () => selectedUidsRef.current,
        activeTab: () => activeTabRef.current,
        requestRender: () => {
          ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
        },
        // Sized against the camera the resize handles measure from, so the
        // ball is never smaller than the blue corner cube on its point.
        camera: () => cameraRef.current,
        // The selected patch's anchor — a model patch or a SERVER patch: the
        // corner (or centre) the inspector's coordinates describe. Taken from
        // the face handles, which already place it on every surface a patch
        // can sit on (a server's face, a wall-mounted unit's curved front),
        // and mapped from world space into globalGroup's, where the balls hang.
        patchAnchor: () => {
          const node = nodeRef.current
          if (!node) return null
          const model = registriesRef.current.modelMap.get(node.uid)
          // No render root, no ball: a hidden model has nothing to mark.
          if (!model || !registriesRef.current.renderRootMap.get(node.uid)) return null
          return selectedPatchAnchor(node, model, () => {
            const world = faceDragRef.current?.anchorPoint()
            if (!world) return null
            globalGroup.updateWorldMatrix(true, false)
            return globalGroup.worldToLocal(world)
          })
        },
      })
      // For browser verification: which models actually have a ball, which a
      // screenshot cannot tell apart from one hidden inside a rack.
      ;(window as unknown as { __anchorMarkers?: AnchorMarkers | null }).__anchorMarkers =
        anchorMarkersRef.current
    }
    anchorMarkersRef.current.refresh()
  }, [project, selectedUid, selectedUids, selectedNode, activeTab, sceneVersion])

  useEffect(
    () => () => {
      anchorMarkersRef.current?.dispose()
      anchorMarkersRef.current = null
      ;(window as unknown as { __anchorMarkers?: unknown }).__anchorMarkers = null
    },
    [],
  )

  // --- the wall / floor surface-normal arrows -------------------------------
  //
  // Parented to the SCENE, not to `globalGroup`: the arrows are computed from
  // the render root's world matrix, so a scene-level rotate or mirror is
  // already baked into them and a transformed parent would apply it twice.
  //
  // Re-run on the same inputs as the anchor markers plus the store's own
  // notifications, because a face flipped from the inspector reverses the
  // normal without changing the selection — and the arrow is the only readout
  // that flip has.
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    if (!orientationHelperRef.current) {
      orientationHelperRef.current = createOrientationHelper({
        scene,
        project: () => projectRef.current,
        selectedUids: () => selectedUidsRef.current,
        activeTab: () => activeTabRef.current,
        previewActive: () => isPreviewActive(),
        renderRoot: (uid) => registriesRef.current.renderRootMap.get(uid) ?? null,
        findModel: (proj, uid) => store.findConstruction(proj, uid)?.model ?? null,
        requestRender: () => {
          ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
        },
      })
      // For browser verification: a screenshot shows two arrows but not which
      // way the exported solid actually faces.
      ;(window as unknown as { __orientationHelper?: OrientationHelper | null }).__orientationHelper =
        orientationHelperRef.current
    }
    orientationHelperRef.current.refresh()
    return store.subscribe(() => orientationHelperRef.current?.refresh())
  }, [project, selectedUid, selectedUids, activeTab])

  useEffect(
    () => () => {
      orientationHelperRef.current?.dispose()
      orientationHelperRef.current = null
      ;(window as unknown as { __orientationHelper?: unknown }).__orientationHelper = null
    },
    [],
  )

  // --- sensor probe spheres ------------------------------------------------
  //
  // Solver-tab only, as `_applyLeftTabUI` gates them
  // (STL_generate.html:19478); the group's own visibility carries that, so the
  // balls keep their positions across a tab switch instead of being rebuilt.
  useEffect(() => {
    const camera = cameraRef.current
    const renderer = rendererRef.current
    const scene = sceneRef.current
    const globalGroup = globalGroupRef.current
    if (!camera || !renderer || !scene || !globalGroup) return
    if (!sensorSpheresRef.current) {
      sensorSpheresRef.current = createSensorSpheres({
        camera,
        scene,
        domElement: renderer.domElement,
        globalGroup,
        orbit: controlsRef.current,
        project: () => projectRef.current,
        activeTab: () => activeTabRef.current,
        requestRender: () => {
          ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
        },
      })
      ;(window as unknown as { __sensorSpheres?: SensorSpheres | null }).__sensorSpheres =
        sensorSpheresRef.current
    }
    sensorSpheresRef.current.refresh()
  }, [project, activeTab])

  useEffect(
    () => () => {
      sensorSpheresRef.current?.dispose()
      sensorSpheresRef.current = null
      ;(window as unknown as { __sensorSpheres?: unknown }).__sensorSpheres = null
    },
    [],
  )

  // Camera-only focus from the visible View tools button. Read live refs so a
  // selection change or scene rebuild never leaves a stale root in the action.
  useEffect(() => installSelectionFocus(() => {
    const camera = cameraRef.current
    const controls = controlsRef.current
    const canvas = rendererRef.current?.domElement
    if (!camera || !controls || !canvas) return null
    return {
      camera, controls,
      area: readUnobscuredFrameArea(canvas),
      roots: registriesRef.current.renderRootMap,
      uids: selectedUidsRef.current,
      helpers: new Set(registriesRef.current.renderHelpers),
      displayRoot: globalGroupRef.current,
      frozen: isUiFrozen(),
      beforeFocus: cancelViewTransition,
      onFocused: () => {
        notifyCameraSettled()
        ;(window as unknown as { __viewportRender?: () => unknown }).__viewportRender?.()
      },
    }
  }), [])

  // --- keyboard: undo, redo, delete, and the gizmo toggles ------------------
  //
  // Installed here because this is the component that already holds the
  // selection and the callback that clears it. Bound once: the handler reads
  // both through refs, so re-binding it per selection change would only churn
  // listeners. Which key does what is the shortcut registry's
  // (`interaction/shortcuts.ts`), read on every key press.
  useEffect(
    () =>
      installEditorKeys({
        selectedUids: () => selectedUidsRef.current,
        clearSelection: () => onSelectRef.current(null),
        frozen: () => isUiFrozen(),
        // A selected pipe waypoint is a finer selection than the pipe, so
        // Delete has to take the waypoint and leave the pipe alone.
        deleteWaypoint: () =>
          (liquidMode() as unknown as { _deleteSelectedWaypoint?: () => boolean } | null)
            ?._deleteSelectedWaypoint?.() === true,
        // R / V: where they are off, and what they act on
        // (`interaction/gizmoShortcutHost.ts`). Every drag this viewport runs
        // counts: a toggle mid-drag would put the uncommitted position on the
        // undo stack and leave the drag writing into a replaced project.
        ...createGizmoShortcutHost({
          activeTab: () => activeTabRef.current,
          selectedUids: () => selectedUidsRef.current,
          node: () => nodeRef.current,
          dragging: () =>
            moveGizmoRef.current?.dragging() === true ||
            rotateGizmoRef.current?.dragging() === true ||
            faceDragRef.current?.dragging() === true ||
            seedDragRef.current?.dragging() === true ||
            airPipeRef.current?.dragging() === true,
        }),
      }),
    [],
  )

  // --- slot guides follow the selection ------------------------------------
  //
  // A rack's U-slot guides are invisible rects the shared builder spawns only
  // for the SELECTED rack — 200 racks x 42 slots x 2 faces would be 16,800
  // transparent meshes otherwise. They are the only handle on a slot, so
  // selecting a rack has to rebuild it, or its slots can never be clicked.
  //
  // TWO racks, not the room. This used to be a full `rebuild()`, which on
  // `case/Alibaba-hongchou` re-created all 287 models and 15,153 meshes for one
  // click — 1.3 s in jsdom with textures off, and several times that in the
  // browser, which is exactly the "click anything and it hangs for ages" the
  // guides-on-demand rule was supposed to prevent. Only the rack that just lost
  // its guides and the one that just gained them are out of date, so only those
  // two are re-rendered (`SceneBuilder.rebuildModels`).
  const lastRackRef = useRef<string | null>(null)
  useEffect(() => {
    const isRack = (uid: string | null) =>
      !!uid && registriesRef.current.modelMap.get(uid)?.kind === 'rack'
    const was = lastRackRef.current
    const now = isRack(selectedUid) ? selectedUid : null
    if (was === now) return
    lastRackRef.current = now
    if (!was && !now) return
    // The builder reads the selection through `selectedRef`, which the
    // highlight effect below assigns — and effects run in source order, so on
    // the first selection this rebuild would otherwise build guides for the
    // PREVIOUS selection and the newly picked rack would have none.
    selectedRef.current = selectedUid
    const touched = [was, now].filter((uid): uid is string => !!uid)
    try {
      builderRef.current?.rebuildModels(touched)
      refreshSupplyFlow(store.getProject() ? allModels(store.getProject()!).map((e) => e.model) : [], globalGroupRef.current, activeTabRef.current)
      // The rebuild threw the preview meshes away and built new ones, so the
      // arrow parented to the old mesh went with them. Measured in the browser:
      // the FIRST click on a region of a not-yet-selected model added the arrow
      // (the preview subscription) and then this effect wiped it, while a
      // second click inside the same model — no model-selection change, no
      // rebuild — left it standing.
      updatePreviewNormalHelper(globalGroupRef.current)
      // The two re-rendered racks came back with fresh materials at full
      // opacity. The builder's `afterBuild` has already put BOTH view fades
      // back on them (it used to be done here, for the post-processing fade
      // only — so with the mesh preview up, selecting a rack brought it back
      // solid over the mesh).
      setSceneMeshes(registriesRef.current.renderMeshes)
      toolsRef.current?.invalidateBounds()
    } catch (err) {
      console.error('[viewport] rebuild for slot guides failed', err)
    }
  }, [selectedUid])

  // --- highlight the selection --------------------------------------------
  // The scene is rebuilt OUTSIDE React's render — a drag's `rebuildNow`, a
  // texture landing — and a rebuild replaces every mesh, tinted ones included.
  // So the highlight also re-runs on the scene bus: measured in the browser, a
  // server patch dragged and released came back white, because the effect's
  // deps had not changed and the new meshes were never painted.
  useEffect(() => {
    selectedRef.current = selectedUid
    // A model is now a GROUP of meshes (a rack is its frame, slots, servers and
    // panels), so the highlight walks the group rather than tinting one box.
    // Materials are cloned on first tint and never shared back: the shared
    // builder caches materials by kind, so tinting one in place would light up
    // every model that happens to use the same preset.
    // Every selected model is tinted, the ACTIVE one brighter: with eight racks
    // selected there has to be a way to see which one the inspector is showing.
    //
    // Which MESHES of a model light up is the selection's business, not the
    // model's: a plain click selects one face and only shift takes the whole
    // model, and the highlight is how the user tells the two apart. So the
    // decision is per mesh, from the narrow selection (`isMeshHighlighted`,
    // the legacy's own table) — tinting the whole group lit a rack for a click
    // on its side, while the inspector was showing a face.
    const selection: HighlightSelection = {
      uid: selectedUid,
      node: selectedNode ?? null,
      uids: new Set(selectedUids ?? (selectedUid ? [selectedUid] : [])),
    }
    const spanCache = new Map<string, EquipmentSpan>()
    const spanFor = (uid: string): EquipmentSpan => {
      let fn = spanCache.get(uid)
      if (!fn) {
        const model = registriesRef.current.modelMap.get(uid)
        const list = model ? equipmentList(model) : []
        fn = (equipmentId) => {
          const hit = list.find((e) => e.id === equipmentId)
          return hit ? { startUnit: hit.startUnit, spanUnits: hit.spanUnits } : null
        }
        spanCache.set(uid, fn)
      }
      return fn
    }
    // Inside a rack the highlight is SOLID blue with the textures dropped
    // (`highlightStyle`): the meshes there are textured, and an emissive tint
    // on a green vent is a green vent. That is a material swap, so the
    // original is kept on the mesh and put back when the highlight goes —
    // the clone is disposed, not left tinted.
    //
    // The fade is the last word on opacity: a highlight under the mesh preview
    // (a selection carried over from the geometry tab) is tinted at the
    // preview's 30%, not painted a 0.9 slab over the mesh, and an un-highlight
    // puts the fade back on the restored material (`paintMeshHighlight`).
    // Kept in a ref so the fade controller can repaint when a fade LANDS —
    // this effect only re-runs when the selection or the scene changes, and a
    // tab switch that brings the preview fade up changes neither.
    paintHighlightsRef.current = () =>
      paintSelectionHighlights(
        registriesRef.current.renderRootMap, selection, spanFor, viewFadesNow(),
        uid => String(registriesRef.current.modelMap.get(uid)?.kind ?? ''),
      )
    paintHighlightsRef.current()
  }, [selectedUid, selectedUids, selectedNode, project, sceneVersion])

  // --- toolbar view options ------------------------------------------------
  useEffect(() => {
    for (const light of lightsRef.current) light.visible = view.lights
    lightsEnabledRef.current = view.lights
    ppLightRef.current?.()
  }, [view.lights])

  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    if (gridRef.current) {
      disposeBackgroundGrid(gridRef.current)
      gridRef.current = null
    }
    if (!view.showGrid) return
    // The toolbar slider is in millimetres; the scene is in metres.
    const grid = buildBackgroundGrid(view.gridSpacingMm / 1000)
    scene.add(grid)
    gridRef.current = grid
  }, [view.showGrid, view.gridSpacingMm])

  return (
    <>
      <div className="viewport" ref={hostRef} style={{ width: '100%', height: '100%' }} />
      <AxisGizmo />
      <div className="viewport-stats">
        <span id="fps-display">FPS: {stats.fps}</span> | meshes: {stats.meshes}
      </div>
    </>
  )
}
