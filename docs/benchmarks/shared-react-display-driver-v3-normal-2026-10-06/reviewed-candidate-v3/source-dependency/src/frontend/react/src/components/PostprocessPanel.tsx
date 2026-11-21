import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import {
  COLOR_MAPS,
  SLICE_NORMALS,
  defaultPostprocessState,
  type ColorMap,
  type PostprocessState,
} from '../postprocess/ppConfig'
import type { PostprocessLayers, SliceLayer, StreamlineLayer } from '../postprocess/ppLayers'
import * as pp from '../postprocess'
import { fieldRange as ppFieldRange, meshBounds } from '../postprocess/ppRenderer'
import {
  getPpView,
  setPpAiBoxDrag,
  setPpPatches,
  setPpRingDrag,
  subscribePpView,
} from '../postprocess/ppViewState'
import { attachAiBoxDrag, attachSliceRingDrag } from '../postprocess/ppViewportDrag'
import { abortable } from '../postprocess/ppAbort'
import { subscribePpClearAll } from '../postprocess/ppClearAll'
import { ppLog, volumeSummary } from '../postprocess/ppLog'
import type { AiBoxBounds, Vec3 } from '../postprocess/ppDragMath'
import { formatColorbarRange, scalarFieldUnit, fromDisplay, isTemperatureField, subscribeTempUnit, tempUnit, tempUnitLabel, toDisplay } from '../postprocess/ppUnits'
import { useTranslation } from '../useTranslation'
import * as store from '../projectStore'
import { nearestIndex, projectIsTransient } from '../transient/resultPlayback'
import { AiInferencePanel } from './AiInferencePanel'
import {
  PpSliceEditDialog,
  PpStreamlineEditDialog,
  type SliceEditForm,
  type StreamlineEditForm,
} from './PpEditDialogs'
import { ColorbarOverlay } from './ColorbarOverlay'
import { NumberField } from './NumberField'
import { cfdrunExport } from '../legacy/cfdrunBuild'
import { clearResultLayerSet, setLiveAirResultSelection } from '../renderer/resultLayerRevision'
import { publishWindResultFrame, type VerifiedAirResultDescriptor } from '../renderer/windResultProducer'
import { fetchVerifiedAirResultSource } from '../renderer/windResultSourceClient'
import { PpInitTask } from './PpInitTask'
import { normaliseVolumeResolution, SPEED_FIELD, SPEED_LABEL, VOLUME_RESOLUTIONS } from '../postprocess/ppFieldOptions'
import {
  buildFieldGroups,
  fieldValues,
  GROUP_LABEL_KEY,
  metaFromNames,
  type FieldOptionGroup,
} from '../postprocess/ppFieldMenu'

/**
 * The air-cooling post-processing tab.
 *
 * Replicates `#postprocess-settings-panel` (STL_generate.html:488): run
 * selection, surface field rendering, streamline sets, slices, volume
 * rendering. Every control keeps its legacy DOM id so one selector addresses
 * both apps.
 *
 * ## No global render button
 *
 * Each section applies its own change, as in the legacy editor: picking a field
 * re-colours the surface, 应用 takes the typed colour limits, 添加流线 adds a
 * set, 应用 under the slice section computes a cut. A single "render
 * everything" button would recompute a 5.7 MB mesh fetch and every slice to
 * change one colour map.
 *
 * ## Sets, not singletons
 *
 * Streamlines and slices are lists. Their payloads are megabytes each and live
 * in refs keyed by set id — putting them in React state would re-render the
 * panel to hand Three.js buffers it already has.
 *
 * ## What the panel does NOT own
 *
 * None of this state is in the project. The legacy editor keeps it in
 * `window._ppState`, which dies with the page; putting it in the store would
 * make every colour-map change dirty the project and land in the undo stack.
 */

/** The viewport's imperative post-processing API, installed by Viewport.tsx. */
interface PpViewport {
  apply(layers: PostprocessLayers): void
  clear(): void
  setInternalVisible?(visible: boolean): void
}

function viewport(): PpViewport | null {
  return (window as unknown as { __ppViewport?: PpViewport }).__ppViewport ?? null
}

/** Keep a selection if the run has it; otherwise take a sensible first. */
function pick(current: string, available: string[], preferred?: string): string {
  if (available.includes(current)) return current
  if (preferred && available.includes(preferred)) return preferred
  return available[0] ?? current
}

interface RunInfo {
  name: string
  path: string
  timesteps: number[]
  runId: string
  /** A PIMPLE run: its times are seconds the timeline's playhead scrubs. */
  transient: boolean
}

/** What a playhead move carries to the next frame: the colour range and the cuts (plan T5b). */
interface FrameCarry {
  clim: [number, number] | null
  slices: { set: PostprocessState['sliceSets'][number]; params: { normalPreset: string; normal: Vec3; origin: Vec3 } }[]
  streams: {
    set: PostprocessState['streamlineSets'][number]
    params: { centre: Vec3; radius: number; count: number; tubeRadius: number }
  }[]
}

export function PostprocessPanel() {
  const { t } = useTranslation()
  const displayTempUnit = useSyncExternalStore(subscribeTempUnit, tempUnit)
  const [state, setState] = useState<PostprocessState>(defaultPostprocessState)
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  // The run's fields AS REPORTED, types and all. The flat name list in
  // `state.fields` cannot say which name is a vector, and a vector is exactly
  // what has to be expanded into Umag/Ux/Uy/Uz before it can be rendered.
  const [fieldMeta, setFieldMeta] = useState<pp.FieldEntry[]>([])
  // What the init is doing, and what the server said was NaN.
  const [initLines, setInitLines] = useState<string[]>([])
  const [nanFields, setNanFields] = useState<string[]>([])
  const [renderSourceEpoch, setRenderSourceEpoch] = useState(0)
  const verifiedSourceRef = useRef<VerifiedAirResultDescriptor | null>(null)
  const initAbort = useRef<AbortController | null>(null)
  const activeSessionRef = useRef<pp.PostprocessSession | null>(null)
  const [activeSession, setActiveSession] = useState<pp.PostprocessSession | null>(null)
  // The volume render's own task dialog: what it is doing, and the handle that
  // gives up on it. A 128^3 resample is tens of seconds of nothing.
  const [volumeLines, setVolumeLines] = useState<string[]>([])
  const volumeAbort = useRef<AbortController | null>(null)
  // internalMesh, mirrored out of the shared view store. The checkbox lives in
  // the right panel and writes a module store, which does not re-render this
  // subtree — and the colorbars this panel draws have to disappear with the
  // layers they label.
  const [internalVisible, setInternalVisible] = useState(getPpView().internalVisible)
  // The decoded payloads outlive renders and are megabytes each, so they live
  // in refs rather than state.
  const meshRef = useRef<pp.DecodedMesh | null>(null)
  const fieldRef = useRef<{ name: string; values: Float32Array } | null>(null)
  const sliceRef = useRef<Map<number, pp.SlicePayload>>(new Map())
  const streamRef = useRef<Map<number, pp.StreamlineSet>>(new Map())
  const nextId = useRef(1)
  // The resampled grid. Megabytes, and recomputed only when the field or the
  // resolution changes — a colour-map change is a uniform update, not a refetch.
  const volumeRef = useRef<{ field: string; resolution: number; payload: pp.VolumePayload } | null>(null)
  // The colour limits in RAW units, mirroring state so an apply started in the
  // same tick as a field fetch uses the values that fetch just produced.
  //
  // The COLORBAR reads these too, and a ref write does not re-render — so every
  // assignment goes through `setClim`, which keeps a state copy in step. Without
  // it the scale kept showing the previous limits after 应用 while the geometry
  // was already recoloured, and a user reading the numbers would misread every
  // colour on screen.
  const climRef = useRef<[number, number] | null>(null)
  const [climShown, setClimShown] = useState<[number, number] | null>(null)
  const setClim = useCallback((next: [number, number] | null) => {
    climRef.current = next
    setClimShown(next)
  }, [])
  useEffect(() => () => {
    initAbort.current?.abort()
    volumeAbort.current?.abort()
    activeSessionRef.current = null
    verifiedSourceRef.current = null
    setLiveAirResultSelection(null)
    clearResultLayerSet('air-postprocess')
  }, [])
  // The AI inference region, drawn with the result layers so 清除全部渲染
  // takes it away with everything else.
  const [aiBox, setAiBox] = useState<{ min: [number, number, number]; max: [number, number, number] } | null>(null)
  // The result's own bounding box: the preview ring is sized from its diagonal
  // and the default cut plane sits at its centre. Taken from the MESH, not the
  // project — the two diverge the moment a case has geometry outside its walls.
  const boundsRef = useRef<ReturnType<typeof meshBounds> | null>(null)
  // What each set was MADE with, which its list row does not carry: a slice
  // keeps only its label, field and colour limits, and the edit dialog has to
  // reopen the plane it was cut on. Refs rather than state — nothing renders
  // from them until a dialog opens.
  const sliceParamsRef = useRef<Map<number, { normalPreset: string; normal: Vec3; origin: Vec3 }>>(new Map())
  const streamParamsRef = useRef<
    Map<number, { centre: Vec3; radius: number; count: number; tubeRadius: number }>
  >(new Map())
  // The open edit dialogs. The FORM lives here rather than inside the dialog
  // because a preview-ring drag writes into it, and a value the viewport can
  // move cannot be private to a component the viewport knows nothing about.
  const [sliceEdit, setSliceEdit] = useState<{ id: number; form: SliceEditForm } | null>(null)
  const [streamEdit, setStreamEdit] = useState<{ id: number; form: StreamlineEditForm } | null>(null)

  const loadRuns = useCallback(async () => {
    try {
      // scan-case-status rather than /api/pp/runs: it is the only endpoint that
      // reports each run's time directories, which the 迭代步 list needs.
      const resp = await fetch('/api/scan-case-status')
      const body = (await resp.json()) as {
        logRuns?: { name: string; path: string; timesteps?: number[]; runId?: string; transient?: boolean }[]
        error?: string
      }
      if (body.error) throw new Error(body.error)
      const list: RunInfo[] = (body.logRuns ?? []).map((r) => ({
        name: r.name,
        path: r.path,
        timesteps: r.timesteps ?? [],
        runId: r.runId ?? '',
        transient: r.transient === true,
      }))
      setRuns(list)
      setState((s) => {
        if (s.runDir) return s
        // Index 0 is the NEWEST: the server sorts by name descending.
        const first = list[0]
        return first
          ? {
              ...s,
              runDir: first.path,
              timesteps: first.timesteps,
              timestep: first.timesteps.at(-1) ?? null,
            }
          : s
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void loadRuns()
  }, [loadRuns])

  const update = useCallback((fn: (s: PostprocessState) => PostprocessState) => {
    setState(fn)
  }, [])

  // The scalar/vector optgroups the three field selects share, and the flat
  // list of values they offer — which is what a stored choice is checked
  // against and what the AI panel and the edit dialogs take.
  const fieldGroups = useMemo(
    () =>
      buildFieldGroups(
        fieldMeta.length ? fieldMeta : metaFromNames(state.fields.length ? state.fields : ['T', 'U']),
      ),
    [fieldMeta, state.fields],
  )
  const fieldOptions = useMemo(() => fieldValues(fieldGroups), [fieldGroups])
  const renderGroups = (groups: FieldOptionGroup[]) =>
    groups.map((g) => (
      <optgroup key={g.kind} label={t(GROUP_LABEL_KEY[g.kind])}>
        {g.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </optgroup>
    ))

  /**
   * Push the current sets into the viewport.
   *
   * Takes an optional patch so a caller can apply state it has just computed —
   * a React state update does not land until the next render, and applying
   * after `setState` would draw the PREVIOUS set list.
   */
  const applyLayers = useCallback(
    (override?: Partial<PostprocessState>) => {
      const vp = viewport()
      const s = { ...state, ...override }
      if (!vp || !s.initialised || !meshRef.current) {
        clearResultLayerSet('air-postprocess')
        return
      }
      const slices: SliceLayer[] = s.sliceSets
        .map((set): SliceLayer | null => {
          const payload = sliceRef.current.get(set.id)
          if (!payload) return null
          return {
            id: set.id,
            payload,
            visible: set.visible && s.slice.visible,
            colorMap: set.colorMap,
            clim: set.clim,
            opacity: set.opacity,
          }
        })
        .filter((x): x is SliceLayer => !!x)

      const streams: StreamlineLayer[] = s.streamlineSets
        .map((set): StreamlineLayer | null => {
          const payload = streamRef.current.get(set.id)
          if (!payload) return null
          return {
            id: set.id,
            set: payload,
            visible: set.visible && s.streamlines.visible,
            colorMap: set.colorMap,
            clim: set.clim,
            tubeRadius: set.tubeRadius,
            opacity: set.opacity,
          }
        })
        .filter((x): x is StreamlineLayer => !!x)

      vp.apply({
        mesh: meshRef.current,
        fieldValues: s.surface.visible ? (fieldRef.current?.values ?? null) : null,
        surface: {
          visible: s.surface.visible,
          hidden: getPpView().hidden,
          colorMap: s.surface.colorMap,
          climMin: climRef.current?.[0] ?? s.surface.climMin,
          climMax: climRef.current?.[1] ?? s.surface.climMax,
          autoScale: s.surface.autoScale,
        },
        slices,
        streamlines: streams,
        ring: {
          // An open slice dialog OWNS the ring: it is the handle that dialog is
          // meant to be used with, and the legacy ticks 显示预览环 on its behalf
          // when the dialog opens (STL_generate.html:23815).
          show: sliceEdit ? true : s.slice.show && s.slice.showRing,
          origin: sliceEdit ? sliceEdit.form.origin : s.slice.origin,
          normal: sliceEdit ? sliceEdit.form.normal : s.slice.normal,
          // A quarter of the model's diagonal, as in the legacy preview: big
          // enough to read as a plane through the room, small enough not to
          // swallow it.
          radius: (boundsRef.current?.diag ?? 12) * 0.25,
        },
        seed: {
          show: s.streamlines.show && s.streamlines.seedVisible,
          centre: s.streamlines.centre,
          radius: s.streamlines.radius,
        },
        aiBox: aiBox ? { show: true, min: aiBox.min, max: aiBox.max } : { show: false, min: [0, 0, 0], max: [0, 0, 0] },
        volume: {
          // NOT gated on `s.volume.show`. That is the 配置体渲染 disclosure —
          // whether the settings sub-panel is expanded — and gating the payload
          // on it meant collapsing the section to get the panel space back
          // DELETED the volume from the scene, with 显示体渲染 still ticked. The
          // legacy's `ppToggleVolumeConfig` (STL_generate.html:22475) only sets
          // `display` on a div and never touches the mesh. `visible` below is
          // the real control, and `ppLayers` builds on `payload && visible`.
          payload: volumeRef.current?.field === s.volume.field ? volumeRef.current.payload : null,
          visible: s.volume.visible,
          colorMap: s.volume.colorMap,
          clim: volumeClim(s.volume),
          opacity: s.volume.opacity,
          resolution: s.volume.resolution,
        },
      })
      vp.setInternalVisible?.(getPpView().internalVisible)
      const descriptor = verifiedSourceRef.current
      const session = activeSessionRef.current
      const fieldData = fieldRef.current
      const project = store.getProject()
      if (!descriptor || !session || !fieldData || !project?.uid) {
        setLiveAirResultSelection(null)
        clearResultLayerSet('air-postprocess')
        return
      }
      try {
        setLiveAirResultSelection({owner:'air-postprocess',session:{...session},
          surfaceField:s.surface.field,meshReceiptSha256:descriptor.source.meshReceiptSha256})
        publishWindResultFrame({
          descriptor, session,
          currentProject: { uid: project.uid, contentSha256: cfdrunExport().projectContentHashSync(project) },
          mesh: meshRef.current, activeSurfaceField: s.surface.field,
          surface: {
            field: fieldData.name, values: fieldData.values, visible: s.surface.visible,
            colorMap: s.surface.colorMap,
            range: climRef.current ?? [s.surface.climMin, s.surface.climMax],
            opacity: 1, hidden: getPpView().hidden,
          },
          slices: slices.map((cut) => {
            const plane = sliceParamsRef.current.get(cut.id)
            if (!plane) throw new Error(`Slice ${cut.id} has no source plane`)
            return { id: cut.id, payload: cut.payload, origin: plane.origin, normal: plane.normal,
              colorMap: cut.colorMap, range: cut.clim ?? cut.payload.scalarRange,
              opacity: cut.opacity, visible: cut.visible }
          }),
          streamlines: streams.map((line) => ({
            id: line.id, set: line.set, colorMap: line.colorMap,
            range: line.clim ?? line.set.scalarRange, opacity: line.opacity,
            tubeRadius: line.tubeRadius, visible: line.visible,
          })),
        })
      } catch (cause) {
        setLiveAirResultSelection(null)
        clearResultLayerSet('air-postprocess')
        const detail = cause instanceof Error ? cause.message : String(cause)
        ppLog(`OV result layer unavailable: ${detail}`)
      }
    },
    [state, aiBox, sliceEdit, renderSourceEpoch],
  )

  /**
   * The limits the volume is actually DRAWN with.
   *
   * An empty box means "the grid's own range", which is only knowable once a
   * grid has been fetched — so the fallback comes from the payload, exactly as
   * the legacy's `isNaN(userMin) ? sRange[0]` does (STL_generate.html:20326).
   */
  function volumeClim(v: PostprocessState['volume']): [number, number] {
    const range: [number, number] = volumeRef.current?.field === v.field ? volumeRef.current.payload.scalarRange : [0, 1]
    return [v.climMin ?? range[0], v.climMax ?? range[1]]
  }

  // Re-apply whenever the settings change. The payloads are cached in refs, so
  // this rebuilds geometry without refetching anything.
  useEffect(() => {
    applyLayers()
  }, [applyLayers])

  // Re-draw when the right panel's patch checkboxes move. This app draws all 53
  // patches as ONE geometry, so hiding a patch is an index rebuild — there is
  // no per-patch object to set `.visible` on.
  const applyRef = useRef(applyLayers)
  applyRef.current = applyLayers
  const lastHidden = useRef(getPpView().hidden)
  const lastSeedDrag = useRef(getPpView().seedDrag)
  const lastRingDrag = useRef(getPpView().ringDrag)

  // What the viewport handles read while a grab is in progress.
  //
  // Through a ref, not through the closure: re-creating the listeners whenever
  // a number changes would tear down the very drag that is changing it, and the
  // grab would end on the first frame of every pull.
  const liveRef = useRef<{ normal: Vec3; box: AiBoxBounds | null }>({ normal: [0, 1, 0], box: null })
  liveRef.current = {
    normal: sliceEdit ? sliceEdit.form.normal : state.slice.normal,
    box: aiBox,
  }
  const sliceEditRef = useRef(sliceEdit)
  sliceEditRef.current = sliceEdit

  // The two direct-manipulation handles. Attached for as long as this panel is
  // mounted, which is exactly the legacy's `activeTab !== 'postprocess'` guard
  // expressed as a lifetime instead of as a check in every handler.
  useEffect(() => {
    const detachRing = attachSliceRingDrag({
      normal: () => liveRef.current.normal,
      onCommit: (origin) => setPpRingDrag(origin),
    })
    const detachBox = attachAiBoxDrag({
      bounds: () => liveRef.current.box,
      onCommit: (box) => setPpAiBoxDrag(box),
    })
    return () => {
      detachRing()
      detachBox()
    }
  }, [])
  useEffect(
    () =>
      subscribePpView(() => {
        const view = getPpView()
        setInternalVisible(view.internalVisible)
        if (view.hidden !== lastHidden.current) {
          lastHidden.current = view.hidden
          applyRef.current()
        }
        // A finished gizmo drag writes the sphere's new world position here.
        // The X/Y/Z boxes follow it, which is what makes the drag and the
        // numbers one control rather than two that disagree.
        if (view.seedDrag && view.seedDrag !== lastSeedDrag.current) {
          lastSeedDrag.current = view.seedDrag
          const centre = view.seedDrag
          setState((prev) => ({ ...prev, streamlines: { ...prev.streamlines, centre } }))
        }
        // Same round trip for the slice ring, with one extra hop: while the
        // edit dialog is open the ring belongs to IT, so the drag lands in the
        // dialog's origin boxes rather than in the sidebar's.
        if (view.ringDrag && view.ringDrag !== lastRingDrag.current) {
          lastRingDrag.current = view.ringDrag
          const origin = view.ringDrag
          if (sliceEditRef.current) {
            setSliceEdit((prev) => (prev ? { ...prev, form: { ...prev.form, origin } } : prev))
          } else {
            setState((prev) => ({ ...prev, slice: { ...prev.slice, origin } }))
          }
        }
      }),
    [],
  )

  /** Give up on an init that is still running. */
  function cancelInit() {
    initAbort.current?.abort()
  }

  function publishSession(session: pp.PostprocessSession | null) {
    if (activeSessionRef.current?.revision !== session?.revision) {
      setLiveAirResultSelection(null)
      clearResultLayerSet('air-postprocess')
    }
    activeSessionRef.current = session
    setActiveSession(session)
  }

  function requireSession(): pp.PostprocessSession {
    const session = activeSessionRef.current
    if (!session) throw new Error('Reopen the selected postprocess run and time')
    return session
  }

  function assertCurrentSession(session: pp.PostprocessSession) {
    if (activeSessionRef.current !== session) {
      throw new Error('Postprocess revision changed; discard the old response')
    }
  }

  function handleSessionError(error: unknown, session: pp.PostprocessSession | null): string | null {
    if (session && activeSessionRef.current !== session) return null
    const message = error instanceof Error ? error.message : String(error)
    if (/pp_revision_(required|stale)|pp_ai_worker_stale|revision.*mismatch|-> 40[89]/i.test(message)) {
      clearDisplayedFrame()
      setState((prev) => ({ ...prev, initialised: false,
        sliceSets: [], streamlineSets: [], volume: { ...prev.volume, visible: false } }))
      setBusy(null)
    }
    setError(message)
    return message
  }

  /** A newly selected time has no valid old-frame overlays to display. */
  function clearDisplayedFrame() {
    initAbort.current?.abort()
    volumeAbort.current?.abort()
    publishSession(null)
    verifiedSourceRef.current = null
    setLiveAirResultSelection(null)
    clearResultLayerSet('air-postprocess')
    viewport()?.clear()
    meshRef.current = null
    fieldRef.current = null
    boundsRef.current = null
    sliceRef.current.clear()
    streamRef.current.clear()
    sliceParamsRef.current.clear()
    streamParamsRef.current.clear()
    volumeRef.current = null
    nextId.current = 1
    setSliceEdit(null)
    setStreamEdit(null)
    setClim(null)
    setStatus('')
    setError(null)
    setNanFields([])
  }

  /**
   * 清除全部渲染 — the SESSION half of the teardown.
   *
   * The button is in the right panel and empties the Three.js group itself.
   * What it cannot reach is here: the slice and streamline sets with their
   * colorbars, the megabyte payloads behind them, the two edit dialogs, and the
   * id counter. Without this the lists survived a clear — `Slice #1` still
   * listed with its colorbar and nothing drawn, and the next cut came back as
   * #2 as though the first were still there.
   *
   * `initialised: false` too, so 开启后处理 has to be pressed again, exactly as
   * `state.initialized = false` does in the legacy (STL_generate.html:24215).
   */
  useEffect(
    () =>
      subscribePpClearAll(() => {
        initAbort.current?.abort()
        volumeAbort.current?.abort()
        verifiedSourceRef.current = null
        setLiveAirResultSelection(null)
        clearResultLayerSet('air-postprocess')
        publishSession(null)
        sliceRef.current.clear()
        streamRef.current.clear()
        sliceParamsRef.current.clear()
        streamParamsRef.current.clear()
        volumeRef.current = null
        nextId.current = 1
        setSliceEdit(null)
        setStreamEdit(null)
        setClim(null)
        setState((prev) => ({
          ...prev,
          initialised: false,
          sliceSets: [],
          streamlineSets: [],
          volume: { ...prev.volume, visible: false },
        }))
      }),
    [],
  )

  /**
   * Open postprocessing with native OpenFOAM loading, then mesh and field data.
   * `at` is the time to open (the selected one by default); a playhead move
   * passes `carry` so the colour range holds and the cuts are made again.
   */
  async function initPostprocess(at: number | null = state.timestep, carry: FrameCarry | null = null) {
    if (!state.runDir) {
      ppLog(t('pp.no_solver_results'))
      setError(t('pp.no_solver_results'))
      return
    }
    clearDisplayedFrame()
    setState((prev) => ({ ...prev, initialised: false }))
    const controller = new AbortController()
    initAbort.current = controller
    const signal = controller.signal
    const log = (line: string) => setInitLines((prev) => [...prev, line])
    setBusy('init')
    setError(null)
    setStatus('')
    setInitLines([])
    setNanFields([])
    let loadingSession: pp.PostprocessSession | null = null
    try {
      log(t('pp.initializing_backend', { runDir: state.runDir }))
      const res = await pp.initRun(state.runDir, at, signal)
      if (signal.aborted || initAbort.current !== controller) return
      const selectedRun = runs.find((run) => run.path === state.runDir)
      if (!selectedRun?.runId || selectedRun.runId !== res.runId) {
        throw new Error('Postprocess run identity mismatch with the selected case')
      }
      loadingSession = res
      publishSession(res)
      // Provenance is an independent read-only request. A missing certificate
      // disables only OV result layers; Three.js field loading stays usable.
      void fetchVerifiedAirResultSource(res, signal).then((descriptor) => {
        if (signal.aborted || activeSessionRef.current?.revision !== res.revision) return
        verifiedSourceRef.current = descriptor
        if (descriptor) setRenderSourceEpoch((value) => value + 1)
        else clearResultLayerSet('air-postprocess')
      }).catch((cause) => {
        if (signal.aborted || activeSessionRef.current?.revision !== res.revision) return
        verifiedSourceRef.current = null
        clearResultLayerSet('air-postprocess')
        ppLog(`OV result source unavailable: ${String(cause)}`)
      })
      log(res.message + (res.cached ? ' (cached)' : ''))
      ppLog(res.message + (res.cached ? ' (cached)' : ''))
      // A field that reconstructed to NaN loads and renders as a blank
      // surface. Named here, with the advice the legacy gives
      // (STL_generate.html:21391): the variable did not converge, so loosen its
      // residual threshold and solve again.
      setNanFields(res.nan_warnings ?? [])
      for (const field of res.nan_warnings ?? []) ppLog(`⚠️ ${t('pp.nan_warning_detail', { field })}`)
      log(t('pp.loading_mesh_metadata'))
      const meta = await pp.fetchMeshMeta(res, signal)
      if (signal.aborted || initAbort.current !== controller) return
      assertCurrentSession(res)
      const groups = buildFieldGroups(meta.fields ?? [])
      const fields = fieldValues(groups)
      setFieldMeta(meta.fields ?? [])
      log(
        t('pp.loading_mesh_binary', {
          points: meta.totalPoints ?? 0,
          triangles: meta.totalTriangles ?? 0,
        }),
      )
      // A new run invalidates every cache: same names, different data.
      meshRef.current = null
      fieldRef.current = null
      setClim(null)
      sliceRef.current.clear()
      streamRef.current.clear()
      sliceParamsRef.current.clear()
      streamParamsRef.current.clear()
      setSliceEdit(null)
      setStreamEdit(null)
      setPpPatches(meta.patches, state.runDir)

      const surfaceField = pick(state.surface.field, fields)
      meshRef.current = await pp.fetchMesh(meta, res, signal)
      if (signal.aborted || initAbort.current !== controller) return
      assertCurrentSession(res)
      log(t('pp.parsing_mesh_data'))
      log(t('pp.building_patch_geometries', { count: meta.patches.length }))
      boundsRef.current = meshBounds(meshRef.current)
      log(t('pp.rendering_initial_field'))
      const loaded = await loadField(surfaceField, res, signal)
      if (signal.aborted || initAbort.current !== controller) return
      assertCurrentSession(res)
      // Frames along a transient run share one colour range, or the colours
      // rescale from frame to frame and stop meaning the same temperature.
      const held = carry?.clim ? { climMin: carry.clim[0], climMax: carry.clim[1] } : null
      if (carry?.clim) setClim(carry.clim)
      const centre = boundsRef.current.center

      setState((prev) => ({
        ...prev,
        initialised: true,
        fields,
        surface: { ...prev.surface, field: surfaceField, ...(held ?? loaded) },
        streamlines: {
          ...prev.streamlines,
          // `speed` is the server's own name for |U| and is not in the run's
          // field list, so it has to be offered explicitly — checking the
          // stored choice against `fields` alone would silently move the
          // default from |U| to whatever scalar happens to be first.
          field: pick(prev.streamlines.field, [SPEED_FIELD, ...fields], SPEED_FIELD),
          // Seed at the middle of the result rather than at the origin, which
          // for a room modelled in the positive octant is a corner on the floor
          // — every streamline would start in the same stagnant corner.
          centre: prev.streamlines.centre.every((v) => v === 0)
            ? (centre as [number, number, number])
            : prev.streamlines.centre,
        },
        slice: {
          ...prev.slice,
          field: pick(prev.slice.field, fields),
          origin: prev.slice.origin.every((v) => v === 0)
            ? (centre as [number, number, number])
            : prev.slice.origin,
        },
        volume: { ...prev.volume, field: pick(prev.volume.field, fields) },
        sliceSets: [],
        streamlineSets: [],
      }))
      if (carry) await recut(carry, res, signal)
      if (signal.aborted || initAbort.current !== controller) return
      setStatus(`${res.message} · ${meta.patches.length} patches · ${fields.length} fields`)
      // Both lines, in the legacy's order: the field the run opened on and its
      // range (STL_generate.html:21592, reached through ppSwitchField), then
      // the mesh that carries it.
      const fieldUnit = isTemperatureField(surfaceField) ? tempUnitLabel() : scalarFieldUnit(surfaceField)
      const unit = fieldUnit ? ' ' + fieldUnit : ''
      const [shownMin, shownMax] = formatColorbarRange(surfaceField, loaded.climMin, loaded.climMax)
      ppLog('Switched to field ' + surfaceField + ' [' + shownMin + ', ' + shownMax + ']' + unit)
      ppLog(`Mesh loaded, ${meta.totalPoints ?? 0} points, ${meta.totalTriangles ?? 0} triangles`)
    } catch (e) {
      if (initAbort.current !== controller) return
      if (loadingSession && activeSessionRef.current === loadingSession) {
        clearDisplayedFrame()
        setState((prev) => ({ ...prev, initialised: false }))
      }
      // An abort is the user pressing 取消, not a failure: reporting it as an
      // error would put a red line under a button they just pressed.
      if (signal.aborted) {
        setStatus(t('pp.cancel'))
        ppLog('cancelled')
      } else {
        const message = e instanceof Error ? e.message : String(e)
        setError(message)
        ppLog(`Error: ${message}`)
      }
    } finally {
      if (initAbort.current === controller) {
        initAbort.current = null
        setInitLines([])
        setBusy(null)
      }
    }
  }

  /** Cut the carried slices and streamlines again on the new frame, same ids and colour limits. */
  async function recut(carry: FrameCarry, session: pp.PostprocessSession, signal: AbortSignal) {
    for (const { set, params } of carry.slices) {
      const payload = await pp.fetchSlice({ origin: params.origin, normal: params.normal, field: set.field }, session)
      if (signal.aborted) return
      assertCurrentSession(session)
      if (!payload.nTriangles) continue
      sliceRef.current.set(set.id, payload)
      sliceParamsRef.current.set(set.id, params)
      setState((prev) => ({ ...prev, sliceSets: [...prev.sliceSets, { ...set, range: pp.sliceDisplayRange(payload) }] }))
    }
    for (const { set, params } of carry.streams) {
      const lines = await pp.fetchStreamlines(
        { center: params.centre, radius: params.radius, nSeeds: params.count, field: set.field }, session)
      if (signal.aborted) return
      assertCurrentSession(session)
      if (!lines.lines.length) continue
      streamRef.current.set(set.id, lines)
      streamParamsRef.current.set(set.id, params)
      setState((prev) => ({ ...prev, streamlineSets: [...prev.streamlineSets, { ...set, range: lines.scalarRange }] }))
    }
    const ids = [...carry.slices, ...carry.streams].map(({ set }) => set.id + 1)
    nextId.current = Math.max(nextId.current, ...ids)
  }

  /** Fetch a field and take its colour limits from the payload's own header. */
  async function loadField(
    name: string,
    session: pp.PostprocessSession,
    signal?: AbortSignal,
  ): Promise<{ climMin: number; climMax: number }> {
    const f = await pp.fetchField(name, session, signal)
    assertCurrentSession(session)
    fieldRef.current = { name, values: f.values }
    const min = f.info.min
    const max = f.info.max
    const range: [number, number] =
      typeof min === 'number' && typeof max === 'number' && min !== max
        ? [min, max]
        : ppFieldRange(f.values)
    setClim(range)
    return { climMin: range[0], climMax: range[1] }
  }

  async function switchField(name: string) {
    const requestSession = activeSessionRef.current
    setLiveAirResultSelection(null)
    clearResultLayerSet('air-postprocess')
    setBusy('field')
    setError(null)
    try {
      const session = requireSession()
      const clim = await loadField(name, session)
      assertCurrentSession(session)
      setState((prev) => ({ ...prev, surface: { ...prev.surface, field: name, ...clim } }))
      // The RANGE is the point of the line: it is what the colours now mean,
      // and the status text that carried it is gone by the next request.
      const fieldUnit = isTemperatureField(name) ? tempUnitLabel() : scalarFieldUnit(name)
      const unit = fieldUnit ? ' ' + fieldUnit : ''
      const [shownMin, shownMax] = formatColorbarRange(name, clim.climMin, clim.climMax)
      ppLog('Switched to field ' + name + ' [' + shownMin + ', ' + shownMax + ']' + unit)
    } catch (e) {
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`Error: ${message}`)
    } finally {
      if (activeSessionRef.current === requestSession) setBusy(null)
    }
  }

  /** 添加流线 — one integration, appended as its own set. */
  async function addStreamlines() {
    const requestSession = activeSessionRef.current
    setBusy('streamlines')
    setError(null)
    try {
      const session = requireSession()
      const s = state.streamlines
      const requested = s.field === 'U' ? 'speed' : s.field
      ppLog(`Computing streamlines (field=${requested})...`)
      const set = await pp.fetchStreamlines({
        center: s.centre,
        radius: s.radius,
        nSeeds: s.count,
        // 'speed' is |U|, the server's own name for the magnitude — asking for
        // 'U' would request the vector and colour by its first component.
        field: requested,
      }, session)
      assertCurrentSession(session)
      if (!set.lines.length) {
        setError(t('pp.no_streamlines_generated'))
        ppLog(t('pp.no_streamlines_generated'))
        return
      }
      const id = nextId.current++
      streamRef.current.set(id, set)
      streamParamsRef.current.set(id, {
        centre: s.centre,
        radius: s.radius,
        count: s.count,
        tubeRadius: s.tubeRadius,
      })
      setState((prev) => ({
        ...prev,
        streamlineSets: [
          ...prev.streamlineSets,
          {
            id,
            label: `SL#${id} (${set.field}, r=${s.radius.toFixed(1)}, n=${s.count})`,
            visible: true,
            opacity: 1,
            colorMap: s.colorMap,
            tubeRadius: s.tubeRadius,
            field: set.field,
            // Starts at the data's own range, as the legacy set does
            // (STL_generate.html:21893): the colorbar dialog is then what
            // narrows it.
            clim: set.scalarRange,
            range: set.scalarRange,
          },
        ],
      }))
      const label = set.field === 'speed' ? '|U|' : set.field
      const lo = toDisplay(set.field, set.scalarRange[0])
      const hi = toDisplay(set.field, set.scalarRange[1])
      ppLog(
        `${set.lines.length} streamlines (set #${id}), ${label} [${lo.toFixed(2)}, ${hi.toFixed(2)}]`,
      )
    } catch (e) {
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`Streamline Error: ${message}`)
    } finally {
      if (activeSessionRef.current === requestSession) setBusy(null)
    }
  }

  /** 应用 (slice) — one cut, appended as its own set. */
  async function addSlice() {
    if (!state.initialised) {
      ppLog(t('pp.please_init_first'))
      return
    }
    const requestSession = activeSessionRef.current
    setBusy('slice')
    setError(null)
    try {
      const session = requireSession()
      const s = state.slice
      ppLog(t('pp.computing_slice', { normal: s.normal.join(','), origin: s.origin.join(',') }))
      const payload = await pp.fetchSlice({ origin: s.origin, normal: s.normal, field: s.field }, session)
      assertCurrentSession(session)
      if (!payload.nTriangles) {
        setError(t('pp.slice_no_intersection'))
        ppLog(t('pp.slice_no_intersection'))
        return
      }
      const id = nextId.current++
      sliceRef.current.set(id, payload)
      sliceParamsRef.current.set(id, {
        normalPreset: s.normalPreset,
        normal: s.normal,
        origin: s.origin,
      })
      const label =
        s.normalPreset === 'custom'
          ? `Slice #${id} (n=[${s.normal.map((v) => v.toFixed(1)).join(',')}], ${s.field})`
          : `Slice #${id} (${s.normalPreset.toUpperCase()}, ${s.field})`
      // The ROBUST range, not the raw min/max: one outlier cell would otherwise
      // stretch the ramp until the whole cut reads as a single colour.
      const robust = pp.sliceDisplayRange(payload)
      const clim: [number, number] = s.climMin !== s.climMax ? [s.climMin, s.climMax] : robust
      setState((prev) => ({
        ...prev,
        // The typed limits follow the data when they were left at their
        // defaults, so the boxes show the range the cut actually has.
        slice: { ...prev.slice, climMin: clim[0], climMax: clim[1] },
        sliceSets: [
          ...prev.sliceSets,
          {
            id,
            label,
            visible: true,
            opacity: 1,
            colorMap: s.colorMap,
            field: s.field,
            clim,
            // What 自动缩放 returns, so pressing it comes back to the range the
            // slice opened on rather than to the outlier.
            range: robust,
          },
        ],
      }))
      ppLog(
        t('pp.slice_added', {
          id,
          points: payload.nPoints,
          triangles: payload.nTriangles,
          field: s.field,
          min: toDisplay(s.field, payload.scalarRange[0]).toFixed(2),
          max: toDisplay(s.field, payload.scalarRange[1]).toFixed(2),
        }),
      )
    } catch (e) {
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`${t('pp.slice_request_failed')} — ${message}`)
    } finally {
      if (activeSessionRef.current === requestSession) setBusy(null)
    }
  }

  /**
   * 应用 (volume) — resample the field onto a grid and draw it.
   *
   * The colour limits come from a 2-98% PERCENTILE range, not from min/max: one
   * 87 °C cell would otherwise stretch the scale until the real 21-37 °C data
   * washes to a single colour. Same reason the legacy does it.
   *
   * Takes an override because a caller can be a state update that has not
   * landed yet. Confirming an AI inference switches the volume to `AI_T` and
   * renders it in the same tick, and reading `state.volume.field` there would
   * fetch the field the panel was showing BEFORE the inference — the whole
   * point of the call is the field that does not exist in `state` yet.
   */
  async function applyVolume(override?: { field?: string; resolution?: number; clim?: [number, number] }) {
    if (!state.initialised) {
      ppLog(t('pp.please_init_first'))
      return
    }
    const field = override?.field ?? state.volume.field
    const resolution = override?.resolution ?? state.volume.resolution
    const cached = volumeRef.current
    // A colour map or an opacity change re-uses the grid already on the GPU.
    // Only a different field or resolution is worth a dialog — the rest is a
    // uniform update and is done before the dialog could be read.
    if (cached && cached.field === field && cached.resolution === resolution) {
      applyLayers()
      ppLog(t('pp.volume_params_updated'))
      return
    }
    const requestSession = activeSessionRef.current
    const controller = new AbortController()
    volumeAbort.current = controller
    const log = (line: string) => setVolumeLines((prev) => [...prev, line])
    setVolumeLines([])
    setBusy('volume')
    setError(null)
    ppLog(t('pp.computing_volume_render', { field, res: resolution }))
    try {
      const session = requireSession()
      log(t('pp.requesting_volume_data', { field, res: resolution }))
      const payload = await abortable(pp.fetchVolume(field, resolution, session), controller.signal)
      assertCurrentSession(session)
      log(t('pp.building_volume_mesh'))
      volumeRef.current = { field, resolution, payload }
      const range =
        override?.clim ??
        pp.percentileRange(payload.scalars, 0.02, 0.98, payload.sentinel) ??
        payload.scalarRange
      setState((prev) => ({
        ...prev,
        volume: prev.volume.field === field
          ? { ...prev.volume, climMin: range[0], climMax: range[1] }
          : prev.volume,
      }))
      // The FULL range, not the percentile window the colours use: the summary
      // says what is in the grid, and the limits say what is being shown.
      const summary = volumeSummary(
        payload.dimensions,
        field,
        toDisplay(field, payload.scalarRange[0]),
        toDisplay(field, payload.scalarRange[1]),
      )
      log(t('pp.volume_render_complete', { summary }))
      ppLog(t('pp.volume_render_complete', { summary }))
    } catch (e) {
      // Cancel is a button, not a failure: the request is still running on the
      // server and its answer is simply dropped.
      if (controller.signal.aborted) {
        ppLog(t('pp.volume_render_cancelled'))
        return
      }
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`${t('pp.volume_render_failed')} — ${message}`)
    } finally {
      if (volumeAbort.current === controller) {
        volumeAbort.current = null
        setVolumeLines([])
        if (activeSessionRef.current === requestSession) setBusy(null)
      }
    }
  }

  function removeStreamlineSet(id: number) {
    streamRef.current.delete(id)
    streamParamsRef.current.delete(id)
    setStreamEdit((prev) => (prev?.id === id ? null : prev))
    setState((prev) => ({ ...prev, streamlineSets: prev.streamlineSets.filter((s) => s.id !== id) }))
  }

  function removeSlice(id: number) {
    sliceRef.current.delete(id)
    sliceParamsRef.current.delete(id)
    setSliceEdit((prev) => (prev?.id === id ? null : prev))
    setState((prev) => ({ ...prev, sliceSets: prev.sliceSets.filter((s) => s.id !== id) }))
    ppLog(t('pp.slice_deleted', { id }))
  }

  /**
   * Reopen a slice's own parameters, from a double-click on its row.
   *
   * The form starts from what the cut was MADE with, not from the sidebar's
   * current values: the sidebar has usually moved on to the next cut by the
   * time a user comes back to an earlier one, and starting there would silently
   * re-cut this slice somewhere else.
   */
  function openSliceEdit(id: number) {
    const set = state.sliceSets.find((x) => x.id === id)
    const params = sliceParamsRef.current.get(id)
    if (!set || !params) return
    setStreamEdit(null)
    setState((prev) => ({ ...prev, slice: { ...prev.slice, showRing: true } }))
    ppLog(t('pp.editing_slice', { id }))
    setSliceEdit({
      id,
      form: {
        normalPreset: params.normalPreset,
        normal: params.normal,
        origin: params.origin,
        field: set.field,
        colorMap: set.colorMap,
        climMin: set.clim[0],
        climMax: set.clim[1],
      },
    })
  }

  function openStreamlineEdit(id: number) {
    const set = state.streamlineSets.find((x) => x.id === id)
    const params = streamParamsRef.current.get(id)
    if (!set || !params) return
    setSliceEdit(null)
    ppLog(t('pp.editing_streamline_set', { id }))
    setStreamEdit({
      id,
      form: {
        centre: params.centre,
        radius: params.radius,
        count: params.count,
        tubeRadius: set.tubeRadius,
        field: set.field,
        colorMap: set.colorMap,
      },
    })
  }

  /**
   * Re-cut one slice in place, keeping its id.
   *
   * The id is what its colorbar slot, its list position and its layer are keyed
   * on. Deleting and re-adding would move the slice to the end of the list and
   * give it a new colorbar, which is the opposite of editing it.
   */
  async function applySliceEdit() {
    const edit = sliceEdit
    if (!edit) return
    const requestSession = activeSessionRef.current
    setBusy('sliceEdit')
    setError(null)
    ppLog(t('pp.recomputing_slice', { id: edit.id }))
    try {
      const session = requireSession()
      const f = edit.form
      const payload = await pp.fetchSlice({ origin: f.origin, normal: f.normal, field: f.field }, session)
      assertCurrentSession(session)
      if (!payload.nTriangles) {
        setError(t('pp.slice_no_intersection'))
        ppLog(t('pp.slice_no_intersection'))
        return
      }
      sliceRef.current.set(edit.id, payload)
      sliceParamsRef.current.set(edit.id, {
        normalPreset: f.normalPreset,
        normal: f.normal,
        origin: f.origin,
      })
      const robust = pp.sliceDisplayRange(payload)
      const clim: [number, number] = f.climMin !== f.climMax ? [f.climMin, f.climMax] : robust
      const label =
        f.normalPreset === 'custom'
          ? `Slice #${edit.id} (n=[${f.normal.map((v) => v.toFixed(1)).join(',')}], ${f.field})`
          : `Slice #${edit.id} (${f.normalPreset.toUpperCase()}, ${f.field})`
      setState((prev) => ({
        ...prev,
        sliceSets: prev.sliceSets.map((x) =>
          x.id === edit.id
            ? { ...x, label, field: f.field, colorMap: f.colorMap, clim, range: robust }
            : x,
        ),
      }))
      setSliceEdit(null)
      ppLog(
        t('pp.slice_updated', {
          id: edit.id,
          points: payload.nPoints,
          field: f.field,
          min: toDisplay(f.field, payload.scalarRange[0]).toFixed(2),
          max: toDisplay(f.field, payload.scalarRange[1]).toFixed(2),
        }),
      )
    } catch (e) {
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`${t('pp.slice_request_failed')} — ${message}`)
    } finally {
      if (activeSessionRef.current === requestSession) setBusy(null)
    }
  }

  /** Re-integrate one streamline set in place, keeping its id. */
  async function applyStreamlineEdit() {
    const edit = streamEdit
    if (!edit) return
    const requestSession = activeSessionRef.current
    setBusy('streamEdit')
    setError(null)
    ppLog(t('pp.recomputing_streamline_set', { id: edit.id }))
    try {
      const session = requireSession()
      const f = edit.form
      const set = await pp.fetchStreamlines({
        center: f.centre,
        radius: f.radius,
        nSeeds: f.count,
        field: f.field === 'U' ? 'speed' : f.field,
      }, session)
      assertCurrentSession(session)
      if (!set.lines.length) {
        setError(t('pp.no_streamlines_generated'))
        ppLog(t('pp.no_streamlines_generated'))
        return
      }
      streamRef.current.set(edit.id, set)
      streamParamsRef.current.set(edit.id, {
        centre: f.centre,
        radius: f.radius,
        count: f.count,
        tubeRadius: f.tubeRadius,
      })
      setState((prev) => ({
        ...prev,
        streamlineSets: prev.streamlineSets.map((x) =>
          x.id === edit.id
            ? {
                ...x,
                label: `SL#${edit.id} (${set.field}, r=${f.radius.toFixed(1)}, n=${f.count})`,
                field: set.field,
                colorMap: f.colorMap,
                tubeRadius: f.tubeRadius,
                // Back to the new data's own range, as the legacy recompute
                // does (STL_generate.html:22165): the old limits belonged to a
                // different set of lines.
                clim: set.scalarRange,
                range: set.scalarRange,
              }
            : x,
        ),
      }))
      setStreamEdit(null)
      ppLog(
        t('pp.streamline_set_updated', {
          id: edit.id,
          count: set.lines.length,
          field: set.field === 'speed' ? '|U|' : set.field,
          min: toDisplay(set.field, set.scalarRange[0]).toFixed(2),
          max: toDisplay(set.field, set.scalarRange[1]).toFixed(2),
        }),
      )
    } catch (e) {
      const message = handleSessionError(e, requestSession)
      if (message) ppLog(`${t('pp.streamline_request_failed')} — ${message}`)
    } finally {
      if (activeSessionRef.current === requestSession) setBusy(null)
    }
  }

  const activeRun = useMemo(
    () => runs.find((r) => r.path === state.runDir) ?? null,
    [runs, state.runDir],
  )

  // Transient results on the timeline (plan T5b): an opened transient run's
  // frame follows the playhead — the nearest written time; 0/ is the generated
  // initial field, not a result.
  const [playheadT, setPlayheadT] = useState(store.playheadTime())
  useEffect(() => store.subscribePlayhead(() => setPlayheadT(store.playheadTime())), [])
  // Where the playhead was when the shown frame was chosen. The frame follows
  // only once the playhead LEAVES it: opening a run keeps its latest time, and
  // a pick from the time list is not re-targeted by the playhead's grid snap.
  const anchor = useRef<number | null>(null)
  const [frameFromPlayhead, setFrameFromPlayhead] = useState(false)
  const followsPlayhead = !!activeRun?.transient && projectIsTransient()
  function goToTime(target: number) {
    const carry: FrameCarry = {
      clim: climRef.current,
      slices: state.sliceSets.flatMap((set) => {
        const params = sliceParamsRef.current.get(set.id)
        return params ? [{ set, params }] : []
      }),
      streams: state.streamlineSets.flatMap((set) => {
        const params = streamParamsRef.current.get(set.id)
        return params ? [{ set, params }] : []
      }),
    }
    setState((s) => ({ ...s, timestep: target }))
    void initPostprocess(target, carry)
  }
  useEffect(() => {
    if (!followsPlayhead || !state.initialised || busy === 'init' || playheadT === anchor.current) return
    const written = (activeRun?.timesteps ?? []).filter((time) => time > 0)
    const target = written[nearestIndex(written, playheadT)]
    if (target === undefined) return
    // Scrubbing crosses several frames; load the one the playhead stops on.
    const handle = setTimeout(() => {
      anchor.current = playheadT
      setFrameFromPlayhead(true)
      if (target !== state.timestep) goToTime(target)
    }, 250)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playheadT, followsPlayhead, state.initialised, state.timestep, busy])

  function cmapSelect(id: string, value: ColorMap, set: (v: ColorMap) => void) {
    return (
      <select id={id} value={value} onChange={(e) => set(e.target.value as ColorMap)}>
        {COLOR_MAPS.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
    )
  }

  /**
   * A row in `pp-streamline-list` / `pp-slice-list`.
   *
   * Double-clicking the LABEL reopens the set, matching the legacy rows
   * (STL_generate.html:21977, :23079) — including the tooltip, which is the
   * only thing that tells a user the gesture exists.
   */
  function setRow(
    key: string,
    label: string,
    visible: boolean,
    opacity: number,
    onVisible: (v: boolean) => void,
    onOpacity: (v: number) => void,
    onDelete: () => void,
    onEdit: () => void,
  ) {
    return (
      <div className="pp-set-row" key={key} data-set={key}>
        <input
          type="checkbox"
          className="pp-set-visible"
          checked={visible}
          onChange={(e) => onVisible(e.target.checked)}
        />
        <span
          className="pp-set-label"
          title={`${label} (${t('pp.dblclick_to_edit')})`}
          style={{ cursor: 'pointer' }}
          onDoubleClick={onEdit}
        >
          {label}
        </span>
        <input
          type="range"
          className="pp-set-opacity"
          min={0}
          max={1}
          step={0.05}
          value={opacity}
          title={t('pp.opacity')}
          onChange={(e) => onOpacity(Number(e.target.value))}
        />
        <button className="pp-set-delete" onClick={onDelete}>
          ✕
        </button>
      </div>
    )
  }

  const drawnSurface = state.initialised && state.surface.visible && fieldRef.current
  const drawnVolume =
    state.initialised && state.volume.visible && volumeRef.current?.field === state.volume.field

  return (
    <div className="content" id="postprocess-settings-panel">
      {/* ── colorbars ─────────────────────────────────────────────────────
          One per drawn layer, with room for the value and layer title:
          the surface bar closest to the panel, then each slice, then
          each streamline set.

          The slice and streamline bars also answer to internalMesh, which is
          what draws those two layers: a bar left floating beside the viewport
          labels colours that are no longer on screen. The surface bar does not
          — its geometry is the patches, which internalMesh never touched. */}
      {drawnSurface && (
        <ColorbarOverlay
          id="pp-colorbar-surface"
          label={state.surface.field}
          field={state.surface.field}
          colorMap={state.surface.colorMap}
          climMin={climShown?.[0] ?? state.surface.climMin}
          climMax={climShown?.[1] ?? state.surface.climMax}
          rightOffset={320}
          onAutoScale={() => {
            const v = fieldRef.current?.values
            if (!v) return null
            const [min, max] = ppFieldRange(v)
            return { min, max }
          }}
          onApply={(min, max, cmap) => {
            setClim([min, max])
            setState((prev) => ({
              ...prev,
              surface: { ...prev.surface, climMin: min, climMax: max, colorMap: cmap, autoScale: false },
            }))
          }}
        />
      )}
      {state.sliceSets.map(
        (set, i) =>
          set.visible &&
          state.slice.visible &&
          internalVisible && (
            <ColorbarOverlay
              key={set.id}
              id={`pp-colorbar-slice-${set.id}`}
              label={set.label}
              field={set.field}
              colorMap={set.colorMap}
              climMin={set.clim[0]}
              climMax={set.clim[1]}
              rightOffset={320 + 90 * (i + 1)}
              onAutoScale={() => ({ min: set.range[0], max: set.range[1] })}
              onApply={(min, max, cmap) => {
                setState((prev) => ({
                  ...prev,
                  sliceSets: prev.sliceSets.map((x) =>
                    x.id === set.id ? { ...x, clim: [min, max] as [number, number], colorMap: cmap } : x,
                  ),
                }))
              }}
            />
          ),
      )}
      {state.streamlineSets.map(
        (set, i) =>
          set.visible &&
          state.streamlines.visible &&
          internalVisible && (
            <ColorbarOverlay
              key={set.id}
              id={`pp-colorbar-streamlines-${set.id}`}
              label={set.label}
              field={set.field}
              colorMap={set.colorMap}
              climMin={set.clim[0]}
              climMax={set.clim[1]}
              rightOffset={320 + 90 * (state.sliceSets.length + i + 1)}
              onAutoScale={() => ({ min: set.range[0], max: set.range[1] })}
              onApply={(min, max, cmap) => {
                setState((prev) => ({
                  ...prev,
                  streamlineSets: prev.streamlineSets.map((x) =>
                    x.id === set.id ? { ...x, clim: [min, max] as [number, number], colorMap: cmap } : x,
                  ),
                }))
              }}
            />
          ),
      )}
      {/* The volume's own bar. The legacy has ONE global colorbar and repoints
          it at the volume after an apply (`_ppUpdateColorbar`,
          STL_generate.html:20339); with a bar per layer the equivalent is a bar
          of its own, or the volume is drawn against whatever limits the surface
          happens to be using — and at the 2-98% window a volume picks, those
          two are never the same numbers. */}
      {drawnVolume && (
        <ColorbarOverlay
          id="pp-colorbar-volume"
          label={state.volume.field}
          field={state.volume.field}
          colorMap={state.volume.colorMap}
          climMin={volumeClim(state.volume)[0]}
          climMax={volumeClim(state.volume)[1]}
          rightOffset={320 + 90 * (state.sliceSets.length + state.streamlineSets.length + 1)}
          onAutoScale={() => {
            const payload = volumeRef.current?.payload
            if (!payload) return null
            const range =
              pp.percentileRange(payload.scalars, 0.02, 0.98, payload.sentinel) ?? payload.scalarRange
            return { min: range[0], max: range[1] }
          }}
          onApply={(min, max, cmap) => {
            update((prev) => ({
              ...prev,
              volume: { ...prev.volume, climMin: min, climMax: max, colorMap: cmap },
            }))
          }}
        />
      )}

      <div id="air-pp-sections">
        <div className="prop-hint">{t('hint.postprocess_desc')}</div>

        {/* ── run selection ─────────────────────────────────────────────── */}
        <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
          <div className="prop-title" style={{ color: 'var(--accent3)' }}>
            {t('title.result_selection')}
          </div>
          <div className="prop-row">
            <label htmlFor="pp-log-run">{t('label.solve_records')}</label>
            <select
              id="pp-log-run"
              value={state.runDir ?? ''}
              disabled={busy === 'init'}
              onChange={(e) => {
                const runDir = e.target.value
                if (runDir === state.runDir) return
                const run = runs.find((r) => r.path === runDir)
                clearDisplayedFrame()
                update((s) => ({
                  ...s,
                  runDir,
                  initialised: false,
                  timesteps: run?.timesteps ?? [],
                  timestep: run?.timesteps.at(-1) ?? null,
                  sliceSets: [],
                  streamlineSets: [],
                  volume: { ...s.volume, visible: false },
                }))
              }}
            >
              {runs.length === 0 && <option value="">{t('label.no_solve_records')}</option>}
              {runs.map((r) => (
                <option key={r.path} value={r.path}>
                  {r.name}
                </option>
              ))}
            </select>
            <button
              className="btn"
              id="pp-refresh-runs"
              title={t('tooltip.refresh_solve_results')}
              onClick={() => void loadRuns()}
            >
              🔄
            </button>
          </div>
          <div className="prop-row">
            <label htmlFor="pp-timestep">{activeRun?.transient ? t('pp.time_label') : t('label.iteration_step')}</label>
            <select
              id="pp-timestep"
              value={state.timestep ?? ''}
              title={t('pp.latest_complete_time')}
              disabled={busy === 'init'}
              onChange={(e) => {
                const timestep = Number(e.target.value)
                if (timestep === state.timestep) return
                if (followsPlayhead && state.initialised) {
                  // An opened transient run: load that frame and move the playhead to it.
                  store.setPlayhead(timestep)
                  anchor.current = store.playheadTime()
                  setFrameFromPlayhead(false)
                  goToTime(timestep)
                  return
                }
                clearDisplayedFrame()
                update((s) => ({
                  ...s,
                  timestep,
                  initialised: false,
                  sliceSets: [],
                  streamlineSets: [],
                  volume: { ...s.volume, visible: false },
                }))
              }}
            >
              {(activeRun?.timesteps ?? []).length === 0 && (
                <option value="">{t('label.no_timesteps')}</option>
              )}
              {(activeRun?.timesteps ?? []).map((ts) => (
                <option key={ts} value={ts}>
                  {ts}
                </option>
              ))}
            </select>
          </div>
          {activeRun?.transient && state.timestep !== null && (
            <div id="pp-result-time" className="prop-hint">
              {t('pp.result_time', { time: String(state.timestep) })}
              {followsPlayhead && state.timestep !== playheadT
                ? ` ${t(frameFromPlayhead ? 'pp.result_time_nearest' : 'pp.result_time_playhead', { playhead: String(playheadT) })}`
                : ''}
            </div>
          )}
        </div>

        <button
          id="btn-init-postprocess"
          className="btn primary"
          disabled={!state.runDir || busy === 'init'}
          onClick={() => {
            anchor.current = store.playheadTime()
            setFrameFromPlayhead(false)
            void initPostprocess()
          }}
        >
          {busy === 'init' ? t('status.loading') : t('btn.enable_postprocess')}
        </button>
        {busy === 'init' && (
          <PpInitTask
            title={t('pp.loading')}
            lines={initLines}
            warnings={nanFields.map((field) => `⚠️ ${t('pp.field_contains_nan', { field })}`)}
            onCancel={cancelInit}
          />
        )}
        {/* The same dialog the init runs behind. A 128³ resample of a large
            case is tens of seconds of a disabled button otherwise, with no
            stage, no progress and nothing to press but reload. */}
        {busy === 'volume' && (
          <PpInitTask
            title={t('pp.volume_loading')}
            lines={volumeLines}
            warnings={[]}
            onCancel={() => volumeAbort.current?.abort()}
          />
        )}
        <div id="postprocess-init-status" className="prop-hint">
          {status}
        </div>
        {/* The warnings outlive the dialog: a NaN field is why the surface a
            user is now looking at is blank, and that is when they read it. */}
        {nanFields.length > 0 && (
          <div id="pp-nan-warnings" className="prop-hint" style={{ color: '#ffb454' }}>
            {nanFields.map((field) => (
              <div key={field} title={t('pp.nan_warning_detail', { field })}>
                {`⚠️ ${t('pp.field_contains_nan', { field })}`}
              </div>
            ))}
          </div>
        )}
        {error && (
          <div className="prop-hint" style={{ color: '#e06c75' }} id="pp-error">
            {error}
          </div>
        )}

        {/* ── the controls, revealed once a run is loaded ────────────────── */}
        <div id="postprocess-controls" style={{ display: state.initialised ? 'block' : 'none' }}>
          {/* surface field */}
          <fieldset className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
            <div className="prop-title" style={{ color: '#00b4dc' }}>
              {t('pp.surface_field_render')}
            </div>
            {/* Before a run is loaded the options would be the fallback
                T/U list, which is a menu of fields that may not exist in the
                result. The legacy select carries one disabled placeholder
                until init fills it (STL_generate.html:521). */}
            <select
              id="pp-field-select"
              value={state.initialised ? state.surface.field : ''}
              onChange={(e) => void switchField(e.target.value)}
              disabled={busy === 'field' || !state.initialised}
            >
              {state.initialised ? (
                renderGroups(fieldGroups)
              ) : (
                <option value="">{t('pp.please_load_first')}</option>
              )}
            </select>
            {/* Shown in the user's unit, held and applied in Kelvin. */}
            <div className="prop-row">
              <label htmlFor="pp-clim-min">
                Min:{isTemperatureField(state.surface.field) ? ` (${tempUnitLabel()})` : ''}
              </label>
              <NumberField
                key={isTemperatureField(state.surface.field) ? 'clim-min-' + displayTempUnit : 'clim-min-raw'}
                id="pp-clim-min"
                decimals={isTemperatureField(state.surface.field) ? 2 : undefined}
                value={toDisplay(state.surface.field, state.surface.climMin)}
                step={isTemperatureField(state.surface.field) ? 0.1 : Math.pow(10, Math.floor(Math.log10(Math.max(Math.abs(state.surface.climMin), Math.abs(state.surface.climMax), 1e-12))) - 2)}
                onCommit={(v) =>
                  update((s) => ({
                    ...s,
                    surface: { ...s.surface, climMin: fromDisplay(s.surface.field, v), autoScale: false },
                  }))
                }
              />
            </div>
            <div className="prop-row">
              <label htmlFor="pp-clim-max">
                Max:{isTemperatureField(state.surface.field) ? ` (${tempUnitLabel()})` : ''}
              </label>
              <NumberField
                key={isTemperatureField(state.surface.field) ? 'clim-max-' + displayTempUnit : 'clim-max-raw'}
                id="pp-clim-max"
                decimals={isTemperatureField(state.surface.field) ? 2 : undefined}
                value={toDisplay(state.surface.field, state.surface.climMax)}
                step={isTemperatureField(state.surface.field) ? 0.1 : Math.pow(10, Math.floor(Math.log10(Math.max(Math.abs(state.surface.climMin), Math.abs(state.surface.climMax), 1e-12))) - 2)}
                onCommit={(v) =>
                  update((s) => ({
                    ...s,
                    surface: { ...s.surface, climMax: fromDisplay(s.surface.field, v), autoScale: false },
                  }))
                }
              />
            </div>
            <div className="prop-row">
              <label htmlFor="pp-cmap-preset">{t('pp.preset')}</label>
              {cmapSelect('pp-cmap-preset', state.surface.colorMap, (v) => {
                setState((prev) => ({ ...prev, surface: { ...prev.surface, colorMap: v } }))
              })}
            </div>
            <div className="prop-row pp-row-buttons">
              <button
                id="pp-auto-scale"
                className="btn"
                onClick={() => {
                  const v = fieldRef.current?.values
                  if (!v) return
                  const [min, max] = ppFieldRange(v)
                  setClim([min, max])
                  setState((prev) => ({
                    ...prev,
                    surface: { ...prev.surface, climMin: min, climMax: max, autoScale: true },
                  }))
                }}
              >
                {t('pp.auto_scale')}
              </button>
              <button
                id="pp-apply-clim"
                className="btn"
                onClick={() => {
                  setClim([state.surface.climMin, state.surface.climMax])
                  applyLayers()
                }}
              >
                {t('common.apply')}
              </button>
            </div>
            <div className="prop-row">
              <label htmlFor="pp-field-visible">{t('pp.show_surface_field')}</label>
              <input
                type="checkbox"
                id="pp-field-visible"
                checked={state.surface.visible}
                onChange={(e) => {
                  setState((prev) => ({ ...prev, surface: { ...prev.surface, visible: e.target.checked } }))
                }}
              />
            </div>
          </fieldset>

          {/* streamlines */}
          <fieldset className="prop-group" style={{ borderLeftColor: '#98c379' }}>
            <div className="prop-title" style={{ color: '#98c379' }}>
              {t('pp.streamline_settings')}
            </div>
            <div className="prop-row">
              <label htmlFor="pp-sl-show">{t('pp.config_seed_sphere')}</label>
              <input
                type="checkbox"
                id="pp-sl-show"
                checked={state.streamlines.show}
                onChange={(e) => {
                  setState((prev) => ({
                    ...prev,
                    streamlines: { ...prev.streamlines, show: e.target.checked },
                  }))
                }}
              />
            </div>
            <div id="pp-sl-config" style={{ display: state.streamlines.show ? 'block' : 'none' }}>
              {(['x', 'y', 'z'] as const).map((axis, i) => (
                <div className="prop-row" key={axis}>
                  <label htmlFor={`pp-sl-c${axis}`}>{axis.toUpperCase()}:</label>
                  <NumberField
                    id={`pp-sl-c${axis}`}
                    value={state.streamlines.centre[i] as number}
                    step={0.0001}
                    onCommit={(v) =>
                      update((s) => {
                        const centre = [...s.streamlines.centre] as [number, number, number]
                        centre[i] = v
                        return { ...s, streamlines: { ...s.streamlines, centre } }
                      })
                    }
                  />
                </div>
              ))}
              <div className="prop-row">
                <label htmlFor="pp-sl-r">R:</label>
                <NumberField
                  id="pp-sl-r"
                  value={state.streamlines.radius}
                  step={0.0001}
                  min={0.01}
                  onCommit={(v) =>
                    update((s) => ({ ...s, streamlines: { ...s.streamlines, radius: v } }))
                  }
                />
              </div>
              <div className="prop-row">
                <label htmlFor="pp-sl-n">{t('pp.quantity')}</label>
                <NumberField
                  id="pp-sl-n"
                  value={state.streamlines.count}
                  step={1}
                  min={1}
                  max={200}
                  onCommit={(v) =>
                    update((s) => ({ ...s, streamlines: { ...s.streamlines, count: Math.round(v) } }))
                  }
                />
                <label htmlFor="pp-sl-tube">{t('pp.tube_diameter')}</label>
                <NumberField
                  id="pp-sl-tube"
                  value={state.streamlines.tubeRadius}
                  step={0.005}
                  min={0.001}
                  onCommit={(v) =>
                    update((s) => ({ ...s, streamlines: { ...s.streamlines, tubeRadius: v } }))
                  }
                />
              </div>
              <div className="prop-row">
                <label htmlFor="pp-sl-field">{t('pp.sl_color_field')}</label>
                <select
                  id="pp-sl-field"
                  value={state.streamlines.field}
                  onChange={(e) =>
                    update((s) => ({ ...s, streamlines: { ...s.streamlines, field: e.target.value } }))
                  }
                >
                  {/* `speed` sits OUTSIDE both optgroups and first, as the
                      legacy prepends it (STL_generate.html:21292). */}
                  <option value={SPEED_FIELD}>{SPEED_LABEL}</option>
                  {renderGroups(fieldGroups)}
                </select>
              </div>
              <div className="prop-row">
                <label htmlFor="pp-sl-cmap">{t('pp.sl_colormap')}</label>
                {cmapSelect('pp-sl-cmap', state.streamlines.colorMap, (v) =>
                  update((s) => ({ ...s, streamlines: { ...s.streamlines, colorMap: v } })),
                )}
              </div>
              <div className="prop-row pp-row-buttons">
                <button
                  id="pp-add-streamline"
                  className="btn"
                  disabled={busy === 'streamlines'}
                  onClick={() => void addStreamlines()}
                >
                  {busy === 'streamlines' ? t('status.loading') : t('pp.add_streamline')}
                </button>
              </div>
            </div>
            <div id="pp-streamline-list">
              {state.streamlineSets.map((set) =>
                setRow(
                  `sl-${set.id}`,
                  set.label,
                  set.visible,
                  set.opacity,
                  (v) => {
                    setState((prev) => ({
                      ...prev,
                      streamlineSets: prev.streamlineSets.map((x) =>
                        x.id === set.id ? { ...x, visible: v } : x,
                      ),
                    }))
                  },
                  (v) => {
                    setState((prev) => ({
                      ...prev,
                      streamlineSets: prev.streamlineSets.map((x) =>
                        x.id === set.id ? { ...x, opacity: v } : x,
                      ),
                    }))
                  },
                  () => removeStreamlineSet(set.id),
                  () => openStreamlineEdit(set.id),
                ),
              )}
            </div>
            <div
              className="prop-row"
              id="pp-sl-visible-row"
              style={{ display: state.streamlineSets.length ? 'flex' : 'none' }}
            >
              <label htmlFor="pp-sl-visible">{t('pp.show_streamlines')}</label>
              <input
                type="checkbox"
                id="pp-sl-visible"
                checked={state.streamlines.visible}
                onChange={(e) => {
                  setState((prev) => ({
                    ...prev,
                    streamlines: { ...prev.streamlines, visible: e.target.checked },
                  }))
                }}
              />
            </div>
            <div
              className="prop-row"
              id="pp-seed-visible-row"
              style={{ display: state.streamlines.show ? 'flex' : 'none' }}
            >
              <label htmlFor="pp-seed-visible">{t('pp.show_seed_sphere')}</label>
              <input
                type="checkbox"
                id="pp-seed-visible"
                checked={state.streamlines.seedVisible}
                onChange={(e) => {
                  setState((prev) => ({
                    ...prev,
                    streamlines: { ...prev.streamlines, seedVisible: e.target.checked },
                  }))
                }}
              />
            </div>
          </fieldset>

          {/* slices */}
          <fieldset className="prop-group" style={{ borderLeftColor: '#e5c07b' }}>
            <div className="prop-title" style={{ color: '#e5c07b' }}>
              {t('pp.slice_settings')}
            </div>
            <div className="prop-row">
              <label htmlFor="pp-slice-show">{t('pp.config_slice')}</label>
              <input
                type="checkbox"
                id="pp-slice-show"
                checked={state.slice.show}
                onChange={(e) => {
                  setState((prev) => ({ ...prev, slice: { ...prev.slice, show: e.target.checked } }))
                }}
              />
            </div>
            <div className="prop-row">
              <label htmlFor="pp-show-ring">{t('pp.show_preview_ring')}</label>
              <input
                type="checkbox"
                id="pp-show-ring"
                checked={state.slice.showRing}
                onChange={(e) => {
                  setState((prev) => ({ ...prev, slice: { ...prev.slice, showRing: e.target.checked } }))
                }}
              />
            </div>
            <div id="pp-slice-config" style={{ display: state.slice.show ? 'block' : 'none' }}>
              <div className="prop-row">
                <label htmlFor="pp-slice-normal">{t('pp.slice_normal')}</label>
                <select
                  id="pp-slice-normal"
                  value={state.slice.normalPreset}
                  onChange={(e) => {
                    const preset = SLICE_NORMALS.find((n) => n.id === e.target.value)
                    setState((prev) => ({
                      ...prev,
                      slice: {
                        ...prev.slice,
                        normalPreset: e.target.value,
                        // A named preset REPLACES the vector; 'custom' keeps
                        // the three boxes so switching does not reset them.
                        normal:
                          preset && preset.id !== 'custom' ? preset.normal : prev.slice.normal,
                      },
                    }))
                  }}
                >
                  {SLICE_NORMALS.map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.label}
                    </option>
                  ))}
                </select>
              </div>
              <div
                id="pp-slice-custom-normal"
                style={{ display: state.slice.normalPreset === 'custom' ? 'block' : 'none' }}
              >
                {(['x', 'y', 'z'] as const).map((axis, i) => (
                  <div className="prop-row" key={`n${axis}`}>
                    <label htmlFor={`pp-slice-n${axis}`}>n{axis.toUpperCase()}:</label>
                    <NumberField
                      id={`pp-slice-n${axis}`}
                      value={state.slice.normal[i] as number}
                      step={0.1}
                      onCommit={(v) =>
                        update((s) => {
                          const normal = [...s.slice.normal] as [number, number, number]
                          normal[i] = v
                          return { ...s, slice: { ...s.slice, normal, normalPreset: 'custom' } }
                        })
                      }
                    />
                  </div>
                ))}
              </div>
              {/* The single-axis box for a named preset, and the xyz trio for a
                  custom normal — the same two modes the legacy panel shows. */}
              <div
                className="prop-row"
                id="pp-slice-origin-axis"
                style={{ display: state.slice.normalPreset === 'custom' ? 'none' : 'flex' }}
              >
                <label id="pp-slice-origin-label">{t('pp.slice_origin')}</label>
                <NumberField
                  id="pp-slice-origin-single"
                  value={
                    state.slice.origin[
                      state.slice.normalPreset === 'x' ? 0 : state.slice.normalPreset === 'z' ? 2 : 1
                    ] as number
                  }
                  step={0.1}
                  onCommit={(v) =>
                    update((s) => {
                      const axis = s.slice.normalPreset === 'x' ? 0 : s.slice.normalPreset === 'z' ? 2 : 1
                      const origin = [...s.slice.origin] as [number, number, number]
                      origin[axis] = v
                      return { ...s, slice: { ...s.slice, origin } }
                    })
                  }
                />
              </div>
              <div
                id="pp-slice-origin-custom"
                style={{ display: state.slice.normalPreset === 'custom' ? 'block' : 'none' }}
              >
                {(['x', 'y', 'z'] as const).map((axis, i) => (
                  <div className="prop-row" key={`o${axis}`}>
                    <label htmlFor={`pp-slice-o${axis}`}>{axis.toUpperCase()}:</label>
                    <NumberField
                      id={`pp-slice-o${axis}`}
                      value={state.slice.origin[i] as number}
                      step={0.0001}
                      onCommit={(v) =>
                        update((s) => {
                          const origin = [...s.slice.origin] as [number, number, number]
                          origin[i] = v
                          return { ...s, slice: { ...s.slice, origin } }
                        })
                      }
                    />
                  </div>
                ))}
              </div>
              <div className="prop-row">
                <label htmlFor="pp-slice-field">{t('pp.slice_field')}</label>
                <select
                  id="pp-slice-field"
                  value={state.slice.field}
                  onChange={(e) => update((s) => ({ ...s, slice: { ...s.slice, field: e.target.value } }))}
                >
                  {renderGroups(fieldGroups)}
                </select>
              </div>
              <div className="prop-row">
                <label htmlFor="pp-slice-cmap">{t('pp.slice_colormap')}</label>
                {cmapSelect('pp-slice-cmap', state.slice.colorMap, (v) =>
                  update((s) => ({ ...s, slice: { ...s.slice, colorMap: v } })),
                )}
              </div>
              <div className="prop-row">
                <label>{t('pp.slice_range')}</label>
              </div>
              <div className="prop-row">
                <label htmlFor="pp-slice-clim-min">
                  Min:{isTemperatureField(state.slice.field) ? ` (${tempUnitLabel()})` : ''}
                </label>
                <NumberField
                  id="pp-slice-clim-min"
                  key={isTemperatureField(state.slice.field) ? 'pp-slice-clim-min-' + displayTempUnit : 'pp-slice-clim-min-raw'}
                decimals={2}
                  value={toDisplay(state.slice.field, state.slice.climMin)}
                  step={0.1}
                  onCommit={(v) =>
                    update((s) => ({
                      ...s,
                      slice: { ...s.slice, climMin: fromDisplay(s.slice.field, v) },
                    }))
                  }
                />
              </div>
              <div className="prop-row">
                <label htmlFor="pp-slice-clim-max">
                  Max:{isTemperatureField(state.slice.field) ? ` (${tempUnitLabel()})` : ''}
                </label>
                <NumberField
                  id="pp-slice-clim-max"
                  key={isTemperatureField(state.slice.field) ? 'pp-slice-clim-max-' + displayTempUnit : 'pp-slice-clim-max-raw'}
                decimals={2}
                  value={toDisplay(state.slice.field, state.slice.climMax)}
                  step={0.1}
                  onCommit={(v) =>
                    update((s) => ({
                      ...s,
                      slice: { ...s.slice, climMax: fromDisplay(s.slice.field, v) },
                    }))
                  }
                />
              </div>
              <div className="prop-row pp-row-buttons">
                <button
                  id="pp-apply-slice"
                  className="btn"
                  disabled={busy === 'slice'}
                  onClick={() => void addSlice()}
                >
                  {busy === 'slice' ? t('status.loading') : t('pp.apply_slice')}
                </button>
              </div>
            </div>
            <div id="pp-slice-list">
              {state.sliceSets.map((set) =>
                setRow(
                  `slice-${set.id}`,
                  set.label,
                  set.visible,
                  set.opacity,
                  (v) => {
                    setState((prev) => ({
                      ...prev,
                      sliceSets: prev.sliceSets.map((x) => (x.id === set.id ? { ...x, visible: v } : x)),
                    }))
                  },
                  (v) => {
                    setState((prev) => ({
                      ...prev,
                      sliceSets: prev.sliceSets.map((x) => (x.id === set.id ? { ...x, opacity: v } : x)),
                    }))
                  },
                  () => removeSlice(set.id),
                  () => openSliceEdit(set.id),
                ),
              )}
            </div>
            <div
              className="prop-row"
              id="pp-slice-visible-row"
              style={{ display: state.sliceSets.length ? 'flex' : 'none' }}
            >
              <label htmlFor="pp-slice-visible">{t('pp.show_slices')}</label>
              <input
                type="checkbox"
                id="pp-slice-visible"
                checked={state.slice.visible}
                onChange={(e) => {
                  setState((prev) => ({ ...prev, slice: { ...prev.slice, visible: e.target.checked } }))
                }}
              />
            </div>
          </fieldset>

          {/* volume — Apply resamples the field onto a grid (/api/pp/volume) and
              `ppLayers` ray-marches it; the camera position is fed in per frame. */}
          <fieldset className="prop-group" style={{ borderLeftColor: '#c678dd' }}>
            <div className="prop-title" style={{ color: '#c678dd' }}>
              {t('pp.volume_settings')}
            </div>
            <div className="prop-row">
              <label htmlFor="pp-vol-show">{t('pp.config_volume')}</label>
              <input
                type="checkbox"
                id="pp-vol-show"
                checked={state.volume.show}
                onChange={(e) => update((s) => ({ ...s, volume: { ...s.volume, show: e.target.checked } }))}
              />
            </div>
            <div id="pp-vol-config" style={{ display: state.volume.show ? 'block' : 'none' }}>
              <div className="prop-row">
                <label htmlFor="pp-vol-field">{t('pp.vol_field')}</label>
                <select
                  id="pp-vol-field"
                  value={state.volume.field}
                  onChange={(e) => {
                    const field = e.target.value
                    volumeAbort.current?.abort()
                    update((s) => ({ ...s, volume: { ...s.volume, field, climMin: null, climMax: null } }))
                  }}
                >
                  {renderGroups(fieldGroups)}
                </select>
              </div>
              <div className="prop-row">
                <label>{t('pp.vol_resolution')}</label>
                <div className="pp-vol-res-group">
                  {VOLUME_RESOLUTIONS.map((r) => (
                    <label key={r}>
                      <input
                        type="radio"
                        name="pp-vol-res"
                        value={r}
                        checked={normaliseVolumeResolution(state.volume.resolution) === r}
                        onChange={() =>
                          update((s) => ({ ...s, volume: { ...s.volume, resolution: r } }))
                        }
                      />{' '}
                      {r}
                    </label>
                  ))}
                </div>
              </div>
              <div className="prop-row">
                <label htmlFor="pp-vol-opacity">{t('pp.vol_opacity')}</label>
                <input
                  type="range"
                  id="pp-vol-opacity"
                  min={0}
                  max={1}
                  step={0.05}
                  value={state.volume.opacity}
                  onChange={(e) =>
                    update((s) => ({ ...s, volume: { ...s.volume, opacity: Number(e.target.value) } }))
                  }
                />
              </div>
              <div className="prop-row">
                <label htmlFor="pp-vol-cmap">{t('pp.vol_colormap')}</label>
                {cmapSelect('pp-vol-cmap', state.volume.colorMap, (v) =>
                  update((s) => ({ ...s, volume: { ...s.volume, colorMap: v } })),
                )}
              </div>
              <div className="prop-row">
                <label>{t('pp.vol_range')}</label>
              </div>
              {/* Shown in the user's unit and held in Kelvin, as the surface and
                  slice limits above are — and as the legacy volume does through
                  `_ppK2Display` / `_ppDisplay2K` (STL_generate.html:20281). Left
                  raw, these two boxes read `288.15` where every other limit in
                  the panel reads `15.0`, and a user typing the °C they see would
                  set a Kelvin window far below the data. */}
              <div className="prop-row">
                <label htmlFor="pp-vol-clim-min">
                  Min:{isTemperatureField(state.volume.field) ? ` (${tempUnitLabel()})` : ''}
                </label>
                <NumberField
                  id="pp-vol-clim-min"
                  key={'pp-vol-clim-min-' + state.volume.field + '-' + displayTempUnit}
                  decimals={isTemperatureField(state.volume.field) ? 2 : undefined}
                  value={
                    state.volume.climMin === null
                      ? null
                      : toDisplay(state.volume.field, state.volume.climMin)
                  }
                  step={isTemperatureField(state.volume.field) ? 0.1 : Math.pow(10, Math.floor(Math.log10(Math.max(Math.abs(state.volume.climMin ?? 0), Math.abs(state.volume.climMax ?? 0), 1e-12))) - 2)}
                  onCommit={(v) =>
                    update((s) => ({
                      ...s,
                      volume: { ...s.volume, climMin: fromDisplay(s.volume.field, v) },
                    }))
                  }
                  onClear={() => update((s) => ({ ...s, volume: { ...s.volume, climMin: null } }))}
                />
              </div>
              <div className="prop-row">
                <label htmlFor="pp-vol-clim-max">
                  Max:{isTemperatureField(state.volume.field) ? ` (${tempUnitLabel()})` : ''}
                </label>
                <NumberField
                  id="pp-vol-clim-max"
                  key={'pp-vol-clim-max-' + state.volume.field + '-' + displayTempUnit}
                  decimals={isTemperatureField(state.volume.field) ? 2 : undefined}
                  value={
                    state.volume.climMax === null
                      ? null
                      : toDisplay(state.volume.field, state.volume.climMax)
                  }
                  step={isTemperatureField(state.volume.field) ? 0.1 : Math.pow(10, Math.floor(Math.log10(Math.max(Math.abs(state.volume.climMin ?? 0), Math.abs(state.volume.climMax ?? 0), 1e-12))) - 2)}
                  onCommit={(v) =>
                    update((s) => ({
                      ...s,
                      volume: { ...s.volume, climMax: fromDisplay(s.volume.field, v) },
                    }))
                  }
                  onClear={() => update((s) => ({ ...s, volume: { ...s.volume, climMax: null } }))}
                />
              </div>
              <div className="prop-row pp-row-buttons">
                <button
                  id="pp-apply-volume"
                  className="btn"
                  disabled={busy === 'volume'}
                  onClick={() => void applyVolume()}
                >
                  {busy === 'volume' ? t('status.loading') : t('pp.apply_volume')}
                </button>
              </div>
              <div className="prop-row">
                <label htmlFor="pp-vol-visible">{t('pp.show_volume')}</label>
                <input
                  type="checkbox"
                  id="pp-vol-visible"
                  checked={state.volume.visible}
                  onChange={(e) =>
                    update((s) => ({ ...s, volume: { ...s.volume, visible: e.target.checked } }))
                  }
                />
              </div>
            </div>
          </fieldset>

          {/* AI adaptive-region inference, last in the panel as in the legacy
              layout: it CONSUMES the volume section, switching it on and
              pointing it at the AI_T field the inference produced. */}
          <AiInferencePanel
            runDir={state.runDir}
            initialised={state.initialised}
            session={activeSession}
            onBox={setAiBox}
            onFields={(names, ranges, nextSession) => {
              const previous = activeSessionRef.current
              if (!previous || previous.runId !== nextSession.runId ||
                  previous.timeValue !== nextSession.timeValue) return
              publishSession(nextSession)
              volumeRef.current = null
              const aiField = names.find((n) => n.startsWith('AI_')) ?? names[0]
              if (!aiField) return
              const range = ranges?.[aiField]
              setState((prev) => ({
                ...prev,
                fields: [...new Set([...prev.fields, ...names])],
                volume: {
                  ...prev.volume,
                  show: true,
                  visible: true,
                  field: aiField,
                  ...(range ? { climMin: range[0], climMax: range[1] } : {}),
                },
              }))
              // And RENDER it. Setting the section's state only opens the panel
              // — the grid itself is fetched by `applyVolume`, so without this
              // call the volume showed AI_T over an empty viewport until the
              // user noticed and pressed 应用 themselves. The legacy awaits
              // `ppApplyVolume()` at the end of the same handler (`:20686`).
              void applyVolume({ field: aiField, clim: range })
            }}
          />
        </div>
      </div>

      {sliceEdit && (
        <PpSliceEditDialog
          id={sliceEdit.id}
          form={sliceEdit.form}
          fields={fieldOptions}
          busy={busy === 'sliceEdit'}
          onChange={(patch) =>
            setSliceEdit((prev) => (prev ? { ...prev, form: { ...prev.form, ...patch } } : prev))
          }
          onClose={() => setSliceEdit(null)}
          onApply={() => void applySliceEdit()}
        />
      )}
      {streamEdit && (
        <PpStreamlineEditDialog
          id={streamEdit.id}
          form={streamEdit.form}
          fields={fieldOptions}
          busy={busy === 'streamEdit'}
          onChange={(patch) =>
            setStreamEdit((prev) => (prev ? { ...prev, form: { ...prev.form, ...patch } } : prev))
          }
          onClose={() => setStreamEdit(null)}
          onApply={() => void applyStreamlineEdit()}
        />
      )}
    </div>
  )
}
