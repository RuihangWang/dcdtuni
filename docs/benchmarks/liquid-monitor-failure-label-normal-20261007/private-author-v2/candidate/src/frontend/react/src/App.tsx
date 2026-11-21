import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ChangeEvent,
} from 'react'
import { FeatureTree } from './components/FeatureTree'
import { Viewport } from './components/Viewport'
import { ViewCube } from './components/ViewCube'
import { RendererControl } from './renderer/RendererControl'
import { TrackPanel } from './transient/TrackPanel'
import { Toolbar, type ViewOptions, DEFAULT_VIEW_OPTIONS } from './components/Toolbar'
import { useViewPrefs } from './ui/viewPrefs'
import { CreationToolbar } from './components/CreationToolbar'
import { RackDetailPanel } from './components/RackDetailPanel'
import { MeshPreviewPanel } from './components/MeshPreviewPanel'
import { MultiSelectPanel } from './components/MultiSelectPanel'
import type { TransformToolsApi } from './legacy/transformTools'
import { detailPanelShows, getRackDetail, subscribeRackDetail } from './geometry/rackDetail'
import { findConstruction } from './projectStore'
import { publishSelection } from './selectionBus'
import { inspectorTitle } from './ui/inspectorTitle'
import { applyClick, applyMany } from './interaction/selection'
import { setLiquidModeActive } from './legacy/liquidScene'
import { Inspector } from './components/Inspector'
import { LiquidRuntimeMonitor } from './components/LiquidRuntimeMonitor'
import { ProjectFileDialog } from './components/ProjectFileDialog'
import { Room2DEditor } from './components/Room2DEditor'
import {
  isRoom2dPanelOpen,
  setRoom2dPanelHost,
  subscribeRoom2dPanel,
} from './room2d/room2dPanelSlot'
import { exitEditor as exitRoom2dEditor } from './room2d/room2dApi'
import { LogPanel } from './components/LogPanel'
import { MeshPanel } from './components/MeshPanel'
import { setMeshPreviewVisible } from './run/meshPreviewSession'
import { LiquidResultsPanel } from './components/LiquidResultsPanel'
import { PostprocessPanel } from './components/PostprocessPanel'
import { PostprocessRightPanel } from './components/PostprocessRightPanel'
import { ExportDialog } from './components/ExportDialog'
import { SolverMonitorPanel } from './components/SolverMonitorPanel'
import { SensorPanel } from './components/SensorPanel'
import { PanelResizers } from './components/PanelResizers'
import { installMonitoringBridge } from './run/monitoring'
import type { SceneBuilder } from './interaction/sceneBuilder'
import type { TreeSelection } from './interaction/treeNodes'
import { firstPerson } from './legacy/firstPerson'
import { LiquidExperimentPanel } from './components/LiquidExperimentPanel'
import { SolverPanel } from './components/SolverPanel'
import { SystemPanel } from './components/SystemPanel'
import { allModels, type CfdProject } from './schema/cfdproj'
import {
  isLeftTabHidden,
  leftTabGate,
  leftTabLabelKey,
  LEFT_TABS,
  resolveLeftTab,
  type LeftTab,
} from './ui/leftTabs'
import * as store from './projectStore'
import * as files from './io/projectFiles'
import * as session from './run/session'
import { resetWorkspace } from './workspaceReset'
import { useTranslation } from './useTranslation'
import type { TranslationKey } from './i18n'
import { FrozenInspector } from './transient/FrozenInspector'
import { startResultPlayback } from './transient/resultPlayback'
import { startLiquidRunFollow } from './liquid/liquidRunFollow'

/**
 * App shell for the React editor.
 *
 * The frame — 330px left panel, toolbar, viewport, 340px right panel, 160px log
 * panel — is the legacy editor's, down to the measured pixel values, so the two
 * apps can be screenshotted side by side while panels migrate one at a time.
 * Matching the frame first is what makes those comparisons mean anything: a
 * difference in the picture is then a difference in the panel under test, not
 * in the shell around it.
 *
 * Runs on its own port beside the legacy app and proxies /api to it (see
 * vite.config.ts), so the same project can be open in both.
 */

/**
 * The path the load dialog opens at.
 *
 * Empty by default, and the file browser then starts at the server's own case
 * directory. It used to be a developer's absolute home path baked into the
 * bundle — which is the association chain the anonymous-review guard exists to
 * break, since anything in this file reaches every browser that opens the app.
 * Set VITE_DCDTUNI_DEFAULT_PROJECT to pin one locally.
 */
const DEFAULT_PROJECT = import.meta.env.VITE_DCDTUNI_DEFAULT_PROJECT ?? ''

export type { LeftTab } from './ui/leftTabs'

/**
 * The live scene builder, which the exporters read.
 *
 * Published on `window` by the viewport because STL export is a function of the
 * BUILT meshes — their `userData.exportName` tags are where OpenFOAM patch
 * names live — so an exporter that took only the project would produce a
 * different set of patches from the same file.
 */
function sceneBuilder(): SceneBuilder | null {
  return (window as unknown as { __sceneBuilder?: SceneBuilder }).__sceneBuilder ?? null
}

/**
 * The right column while the solver tab is open.
 *
 * Air mode gets the monitor/sensor sub-tabs; liquid mode gets the liquid
 * monitor with NO sub-tabs, which is what the legacy does — sensor points are
 * an air-side concept and the liquid solve has its own per-cold-plate view
 * (STL_generate.html:19397-19402).
 */
function SolverRightColumn({
  project,
  liquid,
  subTab,
  onSubTab,
}: {
  project: CfdProject | null
  liquid: boolean
  subTab: 'monitor' | 'sensor'
  onSubTab: (v: 'monitor' | 'sensor') => void
}) {
  const { t } = useTranslation()
  if (liquid) return <LiquidRuntimeMonitor />
  return (
    <>
      <div id="solver-right-subtabs" className="subtab-bar">
        <button
          id="srs-monitor-tab"
          className={`subtab${subTab === 'monitor' ? ' active' : ''}`}
          onClick={() => onSubTab('monitor')}
        >
          {t('sm.tab_monitor')}
        </button>
        <button
          id="srs-sensor-tab"
          className={`subtab${subTab === 'sensor' ? ' active' : ''}`}
          onClick={() => onSubTab('sensor')}
        >
          {t('sm.tab_sensor')}
        </button>
      </div>
      {subTab === 'monitor' ? <SolverMonitorPanel /> : <SensorPanel project={project} />}
    </>
  )
}

/** The liquid chart's tree hook, installed by `liquidPostprocessHost`. */
function treeClickPostProcess(uid: string, equipmentId: string | null) {
  ;(
    window as unknown as {
      _lsmOnTreeClickPostProcess?: (uid: string, equipmentId?: string | null) => void
    }
  )._lsmOnTreeClickPostProcess?.(uid, equipmentId)
}

export function App() {
  const { t } = useTranslation()
  const [, forceRender] = useState(0)
  const [projectPath, setProjectPath] = useState(DEFAULT_PROJECT)
  /**
   * The selection, as ONE state object.
   *
   * Two separate states would be read together inside a click handler, and
   * React batches updates — so a second ctrl-click in the same tick would see
   * the selection as it was BEFORE the first and silently drop it. Kept
   * together, the functional update below always applies to the latest.
   */
  const [selection, setSelection] = useState<{ uids: string[]; active: string | null }>({
    uids: [],
    active: null,
  })
  const selectedUid = selection.active
  const selectedUids = selection.uids
  const setSelectedUid = useCallback(
    (uid: string | null) => setSelection({ uids: uid ? [uid] : [], active: uid }),
    [],
  )

  // The narrower selection under a model — a face, a patch, a server. Cleared
  // whenever a model itself is picked, or the inspector would keep showing a
  // patch that belongs to something no longer selected.
  const [selectedNode, setSelectedNode] = useState<TreeSelection | null>(null)

  /**
   * What the panel header calls the thing below it.
   *
   * Both headers used to pick between exactly two strings — 模型设定 or
   * 工程设定 — so a U-slot, a patch, a wall region, a CDU and a multi-selection
   * all announced themselves as "model settings". `inspectorTitle` has held the
   * legacy's eleven branches since the port and was imported by nothing.
   */
  const panelTitle = () => {
    const title = inspectorTitle({
      node: selectedNode,
      kind: selectedUid && project ? (store.findConstruction(project, selectedUid)?.model.kind as string | null) ?? null : null,
      count: selectedUids.length,
    })
    return 'literal' in title ? title.literal : t(title.key, title.vars)
  }

  // Mirror the app's selection onto the bus.
  //
  // `selectionBus` has readers — CreationToolbar's `useAppSelection`, the
  // viewport's node subscription, and the automation contract's
  // `getSelection()` — and had NO publisher, so every one of them saw a
  // permanently empty selection. The panel readers survive on a prop fallback;
  // the contract had nothing to fall back to and reported `null` for a model
  // that was plainly selected on screen.
  useEffect(() => {
    publishSelection({ uid: selectedUid, node: selectedNode })
  }, [selectedUid, selectedNode])
  const [leftTab, setLeftTabState] = useState<LeftTab>('geometry')
  /**
   * The one way the left tab changes.
   *
   * It carries the mesh preview's visibility with it, synchronously and BEFORE
   * the render, because leaving the mesh tab only HIDES the preview — the
   * decoded mesh stays in memory so coming back is a flag rather than another
   * 45-second load (see `meshPreviewSession`).
   *
   * Not an effect on `leftTab`: effects run child-first, so the panel that
   * replaces the right column would already have set its own model opacity —
   * the post-processing panel dims the geometry to 15% on mount — and an
   * effect that put it back to 1 would win and undo it.
   */
  const setLeftTab = useCallback((tab: LeftTab) => {
    setMeshPreviewVisible(tab === 'mesh')
    setLeftTabState(tab)
  }, [])
  const [solverSubTab, setSolverSubTab] = useState<'monitor' | 'sensor'>('monitor')
  const [coolingMode, setCoolingMode] = useState<'air' | 'liquid'>('air')
  // The legacy liquid modules gate every interaction on `isLiquidMode()`, which
  // this app answers from here.
  useEffect(() => setLiquidModeActive(coolingMode === 'liquid'), [coolingMode])
  // Mesh and system leave the bar in liquid mode, so an open one has to be left
  // as well — the legacy rewrites `activeTab` inside `_applyLeftTabBarForMode`
  // for the same reason.
  useEffect(() => {
    setLeftTabState((tab) => {
      const next = resolveLeftTab(tab, coolingMode)
      if (next !== tab) setMeshPreviewVisible(next === 'mesh')
      return next
    })
  }, [coolingMode, setLeftTab])
  // Both modes: `PostprocessRightPanel` picks the air patch list or the liquid
  // run selector itself. The legacy hides #prop-title/#prop-content on this tab
  // in EITHER mode (_applyLeftTabUI, STL_generate.html:19352), so leaving the
  // inspector up in liquid mode was the divergence, not this.
  const ppRight = leftTab === 'postprocess'
  const solverRight = leftTab === 'solver'
  const meshRight = leftTab === 'mesh'
  const [meshSubTab, setMeshSubTab] = useState<'inspector' | 'meshpreview'>('inspector')
  // The mesh panel's shortcut opens the preview sub-tab; it lives in the left
  // column and the sub-tabs are in the right one.
  useEffect(() => {
    const w = window as unknown as { __showMeshPreview?: () => void }
    w.__showMeshPreview = () => setMeshSubTab('meshpreview')
    return () => {
      delete w.__showMeshPreview
    }
  }, [])
  const [rackDetailUid, setRackDetailUid] = useState<string | null>(null)
  useEffect(
    () => subscribeRackDetail(() => setRackDetailUid(getRackDetail().target)),
    [],
  )
  const [fileBarOpen, setFileBarOpen] = useState(true)
  // Which export dialog is open, if any. The two buttons share one dialog: they
  // differ in what they write, not in what has to be chosen.
  const [exportMode, setExportMode] = useState<'stl' | 'package' | null>(null)
  const [creationOpen, setCreationOpen] = useState(true)
  const [view, setView] = useState<ViewOptions>(DEFAULT_VIEW_OPTIONS)
  // Reading and writing both happen inside the file dialog now, which reports
  // its own progress, so the shell no longer carries a busy flag of its own.
  const [error, setError] = useState<string | null>(null)
  // Which half of the project file dialog is open. One component: 加载工程 and
  // 保存工程 differ in what the confirm button does, not in what has to be
  // chosen — the legacy passes the same 'load'/'save' mode for the same reason.
  const [fileDialog, setFileDialog] = useState<'load' | 'save' | null>(null)
  const [room2d, setRoom2d] = useState(false)
  /**
   * Whether a 2D editor — this one, or one `window.room2d.enterEditor()` mounted
   * into a root of its own — has taken the right column.
   *
   * Read from the slot rather than from `room2d` above, because the API can open
   * the editor without going through this component, and the legacy flips the
   * entry button and hands over the right panel either way.
   */
  const room2dOpen = useSyncExternalStore(subscribeRoom2dPanel, isRoom2dPanelOpen)
  // The legacy forces the geometry tab on entry (`room_2d_editor.js:157`): the
  // mesh, solver and post-processing tabs each replace the right column, and
  // the editor's controls would have nowhere to go.
  useEffect(() => {
    if (room2dOpen) setLeftTab('geometry')
  }, [room2dOpen])
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsed, setRightCollapsed] = useState(false)
  /**
   * Bumped by every project load, and used as a `key` on the panels that cache
   * a RUN — a decoded mesh, a built preview, a patch list.
   *
   * Those caches are megabytes held in refs, and no prop change invalidates
   * them, so a panel that stayed mounted across a load would re-apply the
   * previous project's result over the new geometry. Changing the key is React's
   * way of saying "this is a different thing now": the panel is unmounted, its
   * cleanup runs, and it comes back empty.
   */
  const [workspaceEpoch, setWorkspaceEpoch] = useState(0)

  // Collapsing a panel narrows it to zero and moves everything that is measured
  // off its width — the toolbar, the viewport, the collapse handle itself. That
  // is one CSS variable, so setting it on the root element keeps the whole
  // layout consistent instead of each consumer recomputing it.
  // The body classes are the legacy's (STL_generate.html:17476, 17494) and are
  // what tells the stylesheet that a zero width is a COLLAPSE rather than a very
  // narrow panel — the width clamp's 220px floor would otherwise keep a dead
  // band beside the folded panel.
  useEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--left-panel-width', leftCollapsed ? '0px' : '330px')
    root.style.setProperty('--right-panel-width', rightCollapsed ? '0px' : '340px')
    document.body.classList.toggle('panel-left-collapsed', leftCollapsed)
    document.body.classList.toggle('panel-right-collapsed', rightCollapsed)
  }, [leftCollapsed, rightCollapsed])

  // The store is module-level so three subtrees can write to it without a setter
  // threaded through all of them; React only needs to know it changed.
  useEffect(() => store.subscribe(() => forceRender((n) => n + 1)), [])
  // The .cfdrun builder reads the monitoring config off `window`; installed once
  // so the export does not depend on the solver tab being mounted.
  useEffect(() => installMonitoringBridge(), [])
  useEffect(() => session.subscribeSession(() => forceRender((n) => n + 1)), [])
  // Transient results (air frame, liquid sample) follow the timeline's playhead.
  useEffect(() => startResultPlayback(), [])
  // A live liquid experiment that finishes becomes the shown result.
  useEffect(() => startLiquidRunFollow(), [])

  // Ask the backend what it can do, once at start-up. The legacy app calls the
  // same endpoint and gates the mesh/solver tabs on its `allOk`.
  useEffect(() => {
    void session.refreshServerInfo()
  }, [])

  const project: CfdProject | null = store.getProject()
  // The grid and the snap toggles are remembered per project, as the legacy
  // remembers them (`cfdproj.ui.<uid>`): they are how this room is worked on,
  // not a session default. Keyed on the project's uid, so every route that
  // opens one restores them — the file dialog, a remote path, and
  // `window.__dcdtuni.loadProject`, which is the one scripts take.
  useViewPrefs(project?.uid ?? null, view, setView)

  /**
   * The project's model order, which is the order a multi-selection is kept in.
   *
   * Derived once per render into a ref rather than inside `select`: a
   * "select all" on a 287-model case calls the selection path 287 times, and
   * re-walking the project each time made the root row's click O(n^2) before it
   * had drawn anything.
   */
  const modelOrderRef = useRef<string[]>([])
  modelOrderRef.current = project ? allModels(project).map((m) => m.model.uid) : []

  /**
   * One entry point for every click that changes the selection.
   *
   * The tree and the viewport both call it, so ctrl-click means the same thing
   * in both — which it did not when each kept its own idea of "selected".
   */
  const select = useCallback((uid: string | null, toggle = false) => {
    setSelection((prev) =>
      applyClick(prev.uids, prev.active, uid, toggle, modelOrderRef.current),
    )
    // A model click clears the narrower selection (a face, a patch): the
    // inspector would otherwise keep showing a patch of a model no longer
    // selected.
    setSelectedNode(null)
  }, [])

  /**
   * The tree's set gestures — the root row, a category, a group folder, a
   * shift-range — in ONE store update.
   *
   * Without it the tree spells a set out as a run of single clicks, which on a
   * large case is hundreds of updates and hundreds of `allModels` walks. The
   * two paths are pinned to the same result by `applyMany`'s own test.
   */
  const selectMany = useCallback(
    (uids: string[], additive: boolean, active?: string | null) => {
      setSelection((prev) =>
        applyMany(prev.uids, prev.active, uids, additive, modelOrderRef.current, active ?? null),
      )
      setSelectedNode(null)
      // The liquid chart follows the model the inspector follows, so the batch
      // reports its active one rather than every member of it.
      const target = active ?? uids[uids.length - 1] ?? null
      if (target) treeClickPostProcess(target, null)
    },
    [],
  )

  /**
   * Install a validated project, whatever it was read from.
   *
   * The remembered remote path is written here rather than by each caller
   * because it is what the next Save opens at: a project that came off the
   * user's own disk has no server path, so leaving the previous one in place
   * would offer to overwrite a file the user is no longer editing.
   *
   * ## The workspace is emptied first
   *
   * Every load path in the app ends here — the file picker, the server
   * browser, `window._applyProjectData` — so this is the one place a reset can
   * be guaranteed to happen. It runs BEFORE `store.loadProject`, as the legacy
   * editor's own cleanup block does (STL_generate.html:14953): a result surface
   * from the previous project otherwise stays drawn over the new geometry, and
   * the geometry itself stays dimmed to the 15% the post-processing tab set.
   * `resetWorkspace` empties the scene and the module stores; the React state
   * below is the half this component owns.
   */
  const applyLoadedProject = useCallback(
    (
      loaded: CfdProject,
      sourceName: string,
      options: { remotePath?: string; clearRemotePath?: boolean } = {},
    ) => {
      resetWorkspace()
      // The tab the legacy lands on after a load, because `normalizeUiState`
      // forces `session.activeTab` back to 'geometry'
      // (STL_generate.html:3303). It also unmounts every panel that caches a
      // run, which the epoch below then makes explicit rather than incidental.
      setLeftTab('geometry')
      setMeshSubTab('inspector')
      setSolverSubTab('monitor')
      setRoom2d(false)
      setExportMode(null)
      // Both halves of the selection, and the gizmos that follow them: the move
      // gizmo is built from `selectedUid`, so dropping it destroys the gizmo
      // that was pointing at a model of the project being closed.
      setSelection({ uids: [], active: null })
      setSelectedNode(null)
      setWorkspaceEpoch((n) => n + 1)
      // The toolbar's view options are deliberately NOT reset. They are how the
      // user is looking at the scene, not something the closed project left
      // behind — and this port's default grid spacing is 100 mm against the
      // legacy's 600, so forcing the defaults here would put a divergence into
      // every load rather than only into a cold start.

      store.loadProject(loaded, options.remotePath ?? null)
      // The store's own entry records the PATH. A file from the user's disk has
      // none, so its filename is recorded here instead of "(in memory)".
      if (!options.remotePath) store.logOperation('loadProject', sourceName)
      // The load listener restores the saved case directory first, then scans
      // that directory. Scanning here races the restore and reads the old case.
      // The room's tile size is the grid a user lays this room out on, so the
      // snap grid follows it rather than a fixed 100mm that matches nothing.
      // A STORED spacing wins over it — `useViewPrefs` applies that after this,
      // keyed on the uid — which is the legacy's order: persisted prefs
      // override the file's own `ui` (STL_generate.html:14968).
      const tile = loaded.geometry?.tileSize
      if (typeof tile === 'number' && tile > 0) {
        setView((v) => ({ ...v, gridSpacingMm: tile * 1000 }))
      }
      if (options.remotePath) files.rememberRemoteProjectPath(options.remotePath)
      else if (options.clearRemotePath !== false) files.clearCurrentRemoteProjectFile()
      setError(null)
    },
    [],
  )

  /**
   * The legacy `window._applyProjectData`, same name and same signature.
   *
   * Same name on purpose: the parity harness drives both apps from one snippet,
   * and a project loaded through this call in the React app has to be the same
   * project the HTML app gets from the identical call
   * (STL_generate.html:14944). It takes a RAW parsed document — v1 through V4 —
   * and validates before anything is replaced.
   */
  const applyProjectData = useCallback(
    (
      data: unknown,
      sourceName: string,
      options: { remotePath?: string; clearRemotePath?: boolean } = {},
    ): boolean => {
      const checked = files.checkProjectDocument(data)
      if (!checked.ok) {
        setError(t('error.project_load_failed', { error: checked.error }))
        return false
      }
      applyLoadedProject(checked.project, sourceName, options)
      return true
    },
    [applyLoadedProject, t],
  )

  useEffect(() => {
    const w = window as unknown as { _applyProjectData?: typeof applyProjectData }
    w._applyProjectData = applyProjectData
    return () => {
      delete w._applyProjectData
    }
  }, [applyProjectData])

  /**
   * A .cfdproj picked from the machine the BROWSER runs on.
   *
   * The port had no file input at all, so a user whose projects are not on the
   * server's own disk could not open one. This is the legacy `loadProject`
   * change handler (STL_generate.html:15008) — read as text, parse, validate,
   * apply — with the validation doing real work: `migrateCfdprojToV4` fills in
   * every default, so without a check first, picking the wrong .json silently
   * replaces the open project with an empty room.
   */
  const onLocalProjectFile = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const input = e.target
      const file = input.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onload = (ev) => {
        // Cleared before anything else: the input holds on to the file it was
        // given, so picking the SAME file a second time fires no change event
        // and the load would look like it did nothing.
        input.value = ''
        const result = files.readProjectFileText(String(ev.target?.result ?? ''))
        if (!result.ok) {
          setError(t('error.project_load_failed', { error: result.error }))
          return
        }
        applyLoadedProject(result.project, file.name, { clearRemotePath: true })
        setProjectPath('')
      }
      reader.onerror = () => {
        input.value = ''
        setError(t('loadproj.read_failed', { error: reader.error?.message ?? 'read error' }))
      }
      reader.readAsText(file)
    },
    [applyLoadedProject, t],
  )

  const onRemoteLoaded = useCallback(
    (data: unknown, sourceName: string, remotePath: string) => {
      // Closed first, as the legacy dialog is (`_lpConfirmLoad` removes it
      // before applying), so a validation failure is reported over the editor
      // rather than behind a dialog that is still up.
      setFileDialog(null)
      if (!applyProjectData(data, sourceName, { remotePath })) return
      setProjectPath(remotePath)
      // Point the server at the project's own directory, so mesh and solve act
      // on the case the user just opened rather than whatever was set before.
      void session.setCaseDir(files.getPathDirname(remotePath))
    },
    [applyProjectData],
  )

  // Rack detail takes the INSPECTOR's place, not the whole column: the legacy
  // renders it from `renderRackModelInspector`, so the creation toolbar and the
  // 模型设定 header stay put (STL_generate.html:9241).
  // Off t = 0 in a transient project the inspector is frozen but for the
  // tracked fields (transient design §2) — one wrapper covers all three panels.
  const inspectorSlot = (
    <FrozenInspector>{
    selectedUids.length > 1 && project ? (
      // More than one model: the single-model inspector's size and position
      // boxes would write one number to all of them.
      <MultiSelectPanel
        project={project}
        uids={selectedUids}
        tools={
          (window as unknown as { __transformTools?: TransformToolsApi }).__transformTools ?? null
        }
        onSelectionChange={(uids, active) => setSelection({ uids, active })}
      />
    ) : rackDetailUid && project && detailPanelShows(rackDetailUid, selectedUid, selectedNode) ? (
      <RackDetailPanel
        project={project}
        rack={findConstruction(project, rackDetailUid)?.model ?? null}
      />
    ) : (
      <Inspector
        project={project}
        selectedUid={selectedUid}
        node={selectedNode}
        clearSelection={() => setSelection({ uids: [], active: null })}
      />
    )
    }</FrozenInspector>
  )

  return (
    <>
      <div id="left-panel" className="panel" hidden={leftCollapsed}>
        <div
          id="file-bar-header"
          className="header section-cyan collapse-header"
          onClick={() => setFileBarOpen((v) => !v)}
        >
          <span className="collapse-caret" id="file-bar-caret">
            {fileBarOpen ? '▼' : '▶'}
          </span>
          <span className="title">{t('title.file_bar')}</span>
        </div>
        {fileBarOpen && (
          <div className="file-bar">
            <button className="file-btn" onClick={() => setFileDialog('load')}>
              {t('btn.load_project')}
            </button>
            {/* Save opens the dialog rather than writing straight back, exactly
                as `window.saveProject` does (STL_generate.html:14941). Writing
                in place was the only thing the port could do, so a project
                could never be branched off into a variant, and one opened from
                a path the server cannot write could not be saved at all. */}
            <button
              className="file-btn"
              disabled={!project}
              onClick={() => setFileDialog('save')}
            >
              {store.isDirty() ? t('btn.save_project_dirty') : t('btn.save_project')}
            </button>
            {/* The way in is also the way out, as it is in the legacy: entering
                relabels this button through `btn.return_to_3d` and points it at
                `exitEditor(false)` (room_2d_editor.js:206-216). `data-i18n` is
                how the legacy finds the button, so the same selector reaches it
                here. The exit goes through the API rather than `setRoom2d`
                because an editor opened by `window.room2d.enterEditor()` is not
                this component's to close. */}
            <button
              className="file-btn"
              data-i18n={room2dOpen ? 'btn.return_to_3d' : 'btn.room_2d_editor'}
              disabled={!project}
              title={project ? undefined : t('hint.load_project_first')}
              onClick={() => (room2dOpen ? exitRoom2dEditor(false) : setRoom2d(true))}
            >
              {t(room2dOpen ? 'btn.return_to_3d' : 'btn.room_2d_editor')}
            </button>
            {/* The module restyles this button itself, by id, when the mode is
                entered — which is why the id has to match the legacy one. */}
            <button
              id="fps-toggle-btn"
              className="file-btn"
              disabled={!project}
              title={project ? undefined : t('hint.load_project_first')}
              onClick={() => firstPerson()?.toggle()}
            >
              {t('btn.first_person')}
            </button>
            <button
              id="btn-mode-liquid"
              className={`file-btn cooling-mode-btn${coolingMode === 'liquid' ? ' active' : ''}`}
              onClick={() => setCoolingMode('liquid')}
            >
              {t('btn.liquid_mode')}
            </button>
            <button
              id="btn-mode-air"
              className={`file-btn cooling-mode-btn${coolingMode === 'air' ? ' active' : ''}`}
              onClick={() => setCoolingMode('air')}
            >
              {t('btn.air_mode')}
            </button>
          </div>
        )}

        <div id="left-tab-bar">
          {LEFT_TABS.map((tab) => {
            const labelKey = leftTabLabelKey(tab.id, coolingMode)
            const fullLabel = t(labelKey)
            const icon = fullLabel.split(' ')[0]
            const compactKey = `nav.compact.${tab.id}` as TranslationKey
            const gate = leftTabGate(tab.id, coolingMode, {
              validated: session.isValidated(),
              meshReady: session.isMeshReady(),
              solved: session.isPostprocessReady(),
            })
            return (
              <button
                key={tab.id}
                // The legacy id for the same tab, so one selector switches tabs
                // in either app.
                id={`tab-btn-${tab.id}`}
                className={`left-tab-btn${leftTab === tab.id ? ' active' : ''}`}
                aria-label={fullLabel}
                // Hidden, not dropped: the legacy sets `style.display = 'none'`
                // on the same button, so the id stays in the DOM in both apps.
                style={isLeftTabHidden(tab.id, coolingMode) ? { display: 'none' } : undefined}
                disabled={gate !== null}
                title={
                  gate === 'validate'
                    ? t('error.validate_dctwin_first')
                    : gate === 'mesh'
                      ? t('error.mesh_first')
                      : gate === 'solve'
                        ? t('error.solve_first')
                        : fullLabel
                }
                onClick={() => setLeftTab(tab.id)}
              >
                <span className="left-tab-icon" aria-hidden="true">{icon}</span>
                <span className="left-tab-label">{t(compactKey)}</span>
              </button>
            )
          })}
        </div>

        {leftTab === 'geometry' && (
          <FeatureTree
            project={project}
            selectedUid={selectedUid}
            selectedUids={selectedUids}
            // The narrow selection lights its sub-row (a server, a face, a
            // U-slot) the way the legacy tree lights it.
            selectedNode={selectedNode}
            onSelect={(uid, toggle) => {
              select(uid, toggle)
              // Wired to the TREE, not to `select` itself: a viewport click on a
              // CDU should not pop a chart while modelling. A no-op outside
              // liquid mode.
              if (uid) treeClickPostProcess(uid, null)
            }}
            onSelectMany={selectMany}
            onSelectNode={(sel) => {
              setSelectedNode(sel)
              treeClickPostProcess(sel.uid, sel.equipmentId ?? null)
            }}
          />
        )}
        {leftTab === 'mesh' && <MeshPanel project={project} />}
        {/* The solver tab shows the AIR or the LIQUID solver, never both:
            `_applyDisplayMode` (STL_generate.html:14093-14100) hides
            #air-solver-settings in liquid mode and #liquid-cooling-solver-group
            in air mode. Two solvers configured side by side would let the user
            set knobs for the run that is not about to happen. */}
        {leftTab === 'solver' &&
          (coolingMode === 'liquid' ? (
            <LiquidExperimentPanel project={project} />
          ) : (
            <SolverPanel project={project} />
          ))}
        {leftTab === 'system' && <SystemPanel />}
        {/* Post-processing is per-mode: the liquid results panel reads a
            .liquidrun result, the air one reads an OpenFOAM time directory.
            The air half is not migrated yet. */}
        {leftTab === 'postprocess' &&
          (coolingMode === 'liquid' ? (
            <LiquidResultsPanel key={workspaceEpoch} />
          ) : (
            <PostprocessPanel key={workspaceEpoch} />
          ))}

        {error && <div className="error">{error}</div>}

        {(leftTab === 'geometry' || leftTab === 'mesh') && <div id="left-geo-export">
          <button
            id="btn-export-stl"
            className="btn success"
            disabled={!project}
            title={project ? undefined : t('hint.load_project_first')}
            onClick={() => setExportMode('stl')}
          >
            {t('btn.export_stl')}
          </button>
          <button
            id="btn-export-package"
            className="btn success cfd-package"
            disabled={!project}
            title={project ? undefined : t('hint.load_project_first')}
            onClick={() => setExportMode('package')}
          >
            {t('btn.export_cfd_package')}
          </button>
        </div>}
      </div>

      <Toolbar
        view={view}
        onChange={setView}
        hasSelection={!!selectedUid}
        selectedUid={selectedUid}
        selectedUids={selectedUids}
        project={project}
      />

      <div id="center-view">
        <Viewport
          project={project}
          selectedUid={selectedUid}
          selectedUids={selectedUids}
          view={view}
          onSelect={select}
          onSelectNode={setSelectedNode}
          selectedNode={selectedNode}
          activeTab={leftTab}
        />
        <RendererControl project={project} selectedUids={selectedUids}
          activeTab={leftTab} airMode={coolingMode === 'air'} view={view}
          onSelection={uids=>selectMany(uids,false)}/>
        <div id="view-cube-panel"><ViewCube /></div>
      </div>

      {/* The selected object's parameter tracks, hanging under the top bar;
          opened from the timeline strip (transient projects only). */}
      <TrackPanel coolingMode={coolingMode} />

      <div id="right-panel" className="panel" hidden={rightCollapsed}>
        {/* The post-processing tab REPLACES the right column rather than adding
            to it: the legacy editor hides the creation toolbar and the
            inspector and shows #dctwin-postprocess-right in their place
            (_applyLeftTabUI, STL_generate.html:19618). Keeping the creation
            buttons visible while looking at a result would offer to add a rack
            to a solved case. */}
        {meshRight ? (
          /* The mesh tab's right column is a pair of sub-tabs once a mesh has
             been loaded: the inspector, or the preview. `_applyLeftTabUI`
             shows #right-panel-tabs only while `_meshPreviewState.active`
             (STL_generate.html:19426), so the bar appears with the preview and
             not before. */
          <>
            <div id="right-panel-tabs" className="subtab-bar">
              <button
                id="rpt-inspector-tab"
                className={`subtab${meshSubTab === 'inspector' ? ' active' : ''}`}
                onClick={() => setMeshSubTab('inspector')}
              >
                {t('inspector.title')}
              </button>
              <button
                id="rpt-meshpreview-tab"
                className={`subtab${meshSubTab === 'meshpreview' ? ' active' : ''}`}
                onClick={() => setMeshSubTab('meshpreview')}
              >
                {t('mp.header')}
              </button>
            </div>
            {meshSubTab === 'meshpreview' ? (
              <MeshPreviewPanel key={workspaceEpoch} />
            ) : (
              <>
                {/* The legacy keeps #prop-title above the inspector on every
                    tab that shows it, this one included. */}
                <div className="header" id="prop-title">
                  <span className="title">
                    {panelTitle()}
                  </span>
                </div>
                {inspectorSlot}
              </>
            )}
          </>
        ) : ppRight ? (
          <PostprocessRightPanel key={workspaceEpoch} project={project} />
        ) : solverRight ? (
          /* The solver tab takes the whole right column too: the legacy hides
             the creation toolbar AND #prop-title/#prop-content here
             (_applyLeftTabUI, STL_generate.html:19387-19390), leaving the
             sub-tabs above the monitor. Keeping the inspector below them would
             halve the residual plot — the thing being watched during a solve. */
          <SolverRightColumn
            project={project}
            liquid={coolingMode === 'liquid'}
            subTab={solverSubTab}
            onSubTab={setSolverSubTab}
          />
        ) : (
          <>
        {/* Creation buttons belong to the geometry tab only: `_applyLeftTabUI`
            sets #creation-toolbar-header to `isGeo ? '' : 'none'`
            (STL_generate.html:19338). Offering "new rack" while looking at a
            transient scenario is an invitation to edit the wrong thing. */}
        {leftTab === 'geometry' && (
          <div
            id="creation-toolbar-header"
            className="header section-cyan collapse-header"
            onClick={() => setCreationOpen((v) => !v)}
          >
            <span className="collapse-caret" id="creation-toolbar-caret">
              {creationOpen ? '▼' : '▶'}
            </span>
            <span className="title" id="creation-toolbar-title">
              {coolingMode === 'liquid' ? t('toolbar.liquid_tools') : t('toolbar.create_geometry')}
            </span>
          </div>
        )}
        {leftTab === 'geometry' && creationOpen && (
          <CreationToolbar
            mode={coolingMode}
            project={project}
            onCreated={setSelectedUid}
            selection={{ uid: selectedUid, node: selectedNode }}
          />
        )}
        {/* The 2D editor BORROWS this column, the way the legacy rewrites
            #prop-title and #prop-content while the plan is open
            (room_2d_editor.js:1955). The plan's own controls arrive in the host
            below through a portal; leaving the 3D inspector up beside them was
            two side panels over one scene, and the inspector was describing a
            selection the user could no longer see. The creation toolbar above
            stays, as it does in the legacy. */}
        <div className="header" id="prop-title">
          <span className="title">
            {room2dOpen ? t('btn.room_2d_editor') : panelTitle()}
          </span>
        </div>
        <div id="room2d-side-host" ref={setRoom2dPanelHost} />
        {!room2dOpen && inspectorSlot}
          </>
        )}
      </div>

      <button
        id="left-collapse-btn"
        className="panel-collapse-btn"
        title={t('title.collapse_left')}
        onClick={() => setLeftCollapsed((v) => !v)}
      >
        {leftCollapsed ? '▶' : '◀'}
      </button>
      <button
        id="right-collapse-btn"
        className="panel-collapse-btn"
        title={t('title.collapse_right')}
        onClick={() => setRightCollapsed((v) => !v)}
      >
        {rightCollapsed ? '◀' : '▶'}
      </button>

      {exportMode && project && sceneBuilder() && (
        <ExportDialog
          project={project}
          builder={sceneBuilder() as NonNullable<ReturnType<typeof sceneBuilder>>}
          mode={exportMode}
          onClose={() => setExportMode(null)}
        />
      )}

      {room2d && project && (
        <Room2DEditor project={project} onClose={() => setRoom2d(false)} />
      )}

      {/* The legacy id and accept list (STL_generate.html:119). Always mounted,
          not rendered with the dialog: the dialog CLOSES before opening the OS
          picker, so an input that lived inside it would be gone by the time it
          was clicked. */}
      <input
        type="file"
        id="file-proj"
        accept=".cfdproj, .json"
        style={{ display: 'none' }}
        onChange={onLocalProjectFile}
      />

      {fileDialog && (
        <ProjectFileDialog
          mode={fileDialog}
          project={project}
          currentProjectPath={projectPath}
          // The directory of the open project, else the last one browsed, else
          // the server's own case dir. Falling through to no path at all lands
          // the browser in the server process's home directory, which is both
          // useless and a place a user has to navigate out of every time.
          startPath={
            (projectPath ? files.getPathDirname(projectPath) : '') ||
            files.getProjectDialogStartPath(project, session.getServerInfo()?.caseDir)
          }
          onLoaded={onRemoteLoaded}
          onSaved={(path) => {
            setFileDialog(null)
            setProjectPath(path)
            store.markSaved(path)
            store.logOperation('saveProject', t('saveproj.remote_saved', { path }))
          }}
          onLocalSaved={(name) => {
            setFileDialog(null)
            store.logOperation('saveProject', t('saveproj.local_saved', { name }))
          }}
          onPickLocalFile={() => {
            setFileDialog(null)
            document.getElementById('file-proj')?.click()
          }}
          onClose={() => setFileDialog(null)}
        />
      )}

      <PanelResizers />

      <LogPanel />
    </>
  )
}
