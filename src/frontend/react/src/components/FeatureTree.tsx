import {
  type DragEvent,
  type MouseEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import { Glyph, Icon } from './Icon'
import {
  type CfdProject,
  type Construction,
  countModels,
  nextSortMode,
  SORT_LABEL_KEYS,
  type SortMode,
} from '../schema/cfdproj'
import { categoryClick, modelsInCategories } from '../interaction/categorySelect'
import { clearTransformTools } from '../interaction/deselectAll'
import * as groupStore from '../interaction/groupStore'
import type { ModelGroup } from '../geometry/modelGroups'
import * as store from '../projectStore'
import { type DropPosition, reorderModel } from '../interaction/treeReorder'
import { type TreeChild, type TreeSelection } from '../interaction/treeNodes'
import { cachedModelChildren } from '../interaction/treeChildrenCache'
import {
  buildTreeLayout,
  findRowForKey,
  rememberedViewportHeight,
  rememberViewportHeight,
  rowWindow,
  windowSlice,
  type LayoutCategory,
  type LayoutChild,
  type LayoutEntry,
  type LayoutGroup,
  type LayoutModel,
  type RowWindow,
  type TreeLayout,
} from '../interaction/treeLayout'
import { isTreeNodeHighlighted } from '../interaction/treeHighlight'
import { subscribeTreeReveal, treeRevealKeys } from '../interaction/treeReveal'
import { registerTreeScroll } from '../interaction/treeScroll'
import {
  OPEN_ALL,
  batchFoldKeys,
  foldAll,
  foldKeys,
  isFolded,
  rootClickAction,
  rootFolded,
  toggleFold,
  type FoldState,
} from '../interaction/treeFold'
import { buildTreeCategories, nextAnchor, rangeSelect, visualModelOrder } from '../interaction/treeVisualOrder'
import { subscribeScene, wallRegionNames } from '../viewportBus'
import { bindPreviewToProject, isPreviewActive, subscribePreview } from '../io/stlPreview'
import { StlPreviewTree } from './StlPreviewTree'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'

/**
 * The geometry tree.
 *
 * Ordering is the part with the most history behind it. The V4 `constructions`
 * buckets preserve insertion order, so a freshly added Rack_C10 always landed at
 * the bottom of its category; the sort badge fixes that as a VIEW setting, and
 * a name sort never touches the project, so export order and file bytes stay
 * put. A DRAG is the other half of the same question: it reorders the project
 * data itself — `reorderModel` rewrites the bucket's key order, which IS the
 * export order — so a drop that landed under a name sort would be re-sorted
 * away on the next render. It therefore switches the view to manual ordering,
 * as the legacy does (STL_generate.html:13093), and says so in the log.
 *
 * The same ordering decides what a SHIFT-click selects: the rows between the
 * anchor and the click. Both come from `buildTreeCategories`, so the range is
 * always the rows the user can see between the two they clicked — see
 * `treeVisualOrder` for where the legacy differs.
 *
 * Scrollbar: see styles.css. A 14px grab area with a 48px minimum thumb,
 * because a 500-node tree otherwise renders an unusable sliver.
 *
 * ## Only the rows on screen exist
 *
 * A large case opens into tens of thousands of rows: `case/Alibaba-hongchou`
 * (287 models) is 25,489 of them and 121,825 DOM nodes. Rendering all of them
 * made every interaction pay for all of them — one click in the 3D view selects
 * a model and reveals its row, and that took seconds. So `treeLayout` numbers
 * every row of the fold-out tree first, and this component draws only the rows
 * a scroll position puts on screen, with spacers standing in for the rest. The
 * markup, the indentation and every `data-*` hook are unchanged; a row that is
 * off screen is simply not in the DOM yet.
 *
 * A container that reports no height — a jsdom test, or the panel collapsed to
 * `display: none` — gets no window and draws every row, which is what it did
 * before this existed. Nothing that cannot be measured draws nothing.
 */

const SORT_STORAGE_KEY = 'dcdtuni-tree-sort'

/** Rows drawn above and below the visible ones, so a fast scroll is not blank. */
const OVERSCAN_ROWS = 12
/** Used until the stylesheet has been read; `--tree-row-h` is the real value. */
const ROW_HEIGHT_FALLBACK = 32
/** `.content`'s own top padding, which `scrollTop` counts and the row grid does not. */
const PAD_TOP_FALLBACK = 12

function readStoredSortMode(): SortMode {
  try {
    const saved = localStorage.getItem(SORT_STORAGE_KEY)
    if (saved === 'name-asc' || saved === 'name-desc' || saved === 'manual') return saved
  } catch {
    /* private mode: fall through to the default */
  }
  return 'name-asc'
}

interface TreeMetrics {
  top: number
  height: number
  rowHeight: number
  padTop: number
}

/** Read the row pitch and the padding back off the stylesheet, not off a copy. */
function measureMetrics(el: HTMLElement, top: number): TreeMetrics {
  const cs = el.ownerDocument.defaultView?.getComputedStyle(el)
  const rowHeight = parseFloat(cs?.getPropertyValue('--tree-row-h') ?? '') || ROW_HEIGHT_FALLBACK
  const padTop = parseFloat(cs?.paddingTop ?? '') || 0
  return { top, height: el.clientHeight, rowHeight, padTop }
}

export interface FeatureTreeProps {
  project: CfdProject | null
  selectedUid: string | null
  /** Every selected model, for the multi-select highlight. */
  selectedUids?: string[]
  /**
   * `toggle` is a ctrl/cmd-click: add to or remove from the selection instead
   * of replacing it.
   */
  onSelect: (uid: string | null, toggle?: boolean) => void
  /**
   * A whole set at once — the root row, a category, a group folder, a
   * shift-range.
   *
   * Optional, and `selectMany` falls back to spelling the set out as a run of
   * `onSelect` clicks when it is absent. The fallback is 287 store updates on a
   * 287-model project, each re-deriving the model order, which is the cost this
   * prop exists to remove.
   */
  onSelectMany?: (uids: string[], additive: boolean, active?: string | null) => void
  /**
   * A sub-node was picked — a face, a patch, a server, a partition.
   *
   * Separate from `onSelect` because the two mean different things: selecting a
   * model is what the viewport highlights and the inspector edits, while a
   * sub-node narrows that to one part of it. A caller that does not handle the
   * narrower selection still gets the model selected, so the tree stays usable
   * before the inspector understands every node type.
   */
  onSelectNode?: (selection: TreeSelection) => void
  /**
   * The narrower selection under the active model — a face, a patch, a server,
   * a U-slot. It lights the sub-rows it names, as the legacy builder lights
   * them (`isTreeNodeHighlighted`); without it every row under a model was drawn
   * plain, so a server face picked in the viewport lit only its rack and the
   * rack category.
   */
  selectedNode?: TreeSelection | null
}

/** Which row a drag is currently over, and which side of it. */
interface DropMark {
  uid: string
  position: DropPosition
}

interface DragHandlers {
  mark: DropMark | null
  start(e: DragEvent, uid: string): void
  over(e: DragEvent, uid: string): void
  drop(e: DragEvent, uid: string): void
  end(): void
}

interface NodeLabels {
  visibility: string
  duplicate: string
  remove: string
  ungroup: string
}

/**
 * Everything a row needs that is not its own layout entry.
 *
 * One object rather than fourteen props per row: the rows are four component
 * types deep, and threading a callback through all of them is how a signature
 * change ends up touching every one. Rebuilt per render, which is free next to
 * what the rows themselves do.
 */
interface TreeCtx {
  win: RowWindow | null
  rowHeight: number
  multi: ReadonlySet<string>
  selectedUid: string | null
  selectedNode: TreeSelection | null
  labels: NodeLabels
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string
  editingGroup: string | null
  setEditingGroup(id: string | null): void
  /** The category rows a category click lit, on top of the model-derived ones. */
  catSelCategories: readonly string[]
  toggle(key: string, open?: boolean, shift?: boolean): void
  clickCategory(e: MouseEvent, bucket: string): void
  clickGroup(e: MouseEvent, group: ModelGroup, members: Construction[]): void
  clickModelRow(e: MouseEvent, uid: string): void
  toggleVisibility(e: { shiftKey?: boolean }, uid: string, checked: boolean): void
  duplicate(uid: string): void
  remove(uid: string, selected: boolean): void
  ungroup(uid: string): void
  renameGroup(id: string, name: string): void
  clickSub(node: TreeChild, children: readonly TreeChild[], open: boolean): void
  dnd: DragHandlers
  onSelect(uid: string | null, toggle?: boolean): void
  onSelectNode?: (selection: TreeSelection) => void
}

export function FeatureTree({
  project,
  selectedUid,
  selectedUids,
  onSelect,
  onSelectMany,
  onSelectNode,
  selectedNode,
}: FeatureTreeProps) {
  const multi = useMemo(
    () => new Set(selectedUids ?? (selectedUid ? [selectedUid] : [])),
    [selectedUids, selectedUid],
  )
  // `locale` feeds the row layout below: the cached rows are translated.
  const { t, locale } = useTranslation()
  // STL Preview replaces this whole panel, as `updateStlPreviewUI` replaces the
  // legacy tree. Subscribed here rather than in App state because the mode is
  // switched from the toolbar, which is a different React subtree.
  const [, bumpPreview] = useState(0)
  useEffect(() => {
    bindPreviewToProject()
    return subscribePreview(() => bumpPreview((n) => n + 1))
  }, [])
  const previewOn = isPreviewActive()
  // Held in a ref so the effect below depends on the MODE and not on the
  // identity of a callback the parent rebuilds on every render.
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  useEffect(() => {
    // Entering the mode resets the selection, as the legacy switch does
    // (STL_generate.html:11745). Without it a multi-selection survives into the
    // preview and the right column shows the batch-transform panel, which
    // edits models the preview is not describing.
    if (previewOn) onSelectRef.current(null)
  }, [previewOn])
  const [sortMode, setSortMode] = useState<SortMode>(readStoredSortMode)
  const [fold, setFold] = useState<FoldState>(OPEN_ALL)
  // Wall-region rows come from the BUILT meshes, so the tree re-renders when the
  // scene does. Without this a wall split into regions shows none of them until
  // something else happens to re-render the panel.
  const [sceneRev, bumpScene] = useState(0)
  useEffect(() => subscribeScene((v) => bumpScene(v)), [])
  // Groups live outside the project (see groupStore), so a group edit does not
  // go through the project store and would otherwise not re-render this panel.
  const [, bumpGroups] = useState(0)
  useEffect(() => {
    // Subscribed BEFORE binding: the bind runs the first sync, which reads the
    // stored groups and emits. A listener registered after it would miss that
    // emit, and a project whose groups were already in storage would render
    // with no folders until something else happened to re-render the panel.
    const off = groupStore.subscribeGroups(() => bumpGroups((n) => n + 1))
    groupStore.bindGroupsToProject()
    return off
  }, [])
  const groups = groupStore.getGroups()
  // Which category rows are highlighted, and the anchor a shift-click extends
  // from — the legacy's `uiState._selectedCategories` / `_anchorCategory`.
  const [catSel, setCatSel] = useState<{ categories: string[]; anchor: string | null }>({
    categories: [],
    anchor: null,
  })
  // The category highlight stands for a MULTI-model selection and cannot
  // outlive it. `setSelection` drops `_selectedCategories` for anything that is
  // not one (STL_generate.html:6294-6296), so in the legacy a single-model
  // click — and a deselect from anywhere, the 3D viewport included — leaves no
  // category row lit. Measured there: `selectCategory('box')` on a category
  // holding one model reports 0 highlighted categories, `('rack')` on fourteen
  // reports 1.
  const multiSelected = multi.size > 1
  useEffect(() => {
    if (multiSelected) return
    setCatSel((prev) => (prev.categories.length ? { categories: [], anchor: null } : prev))
  }, [multiSelected])
  // The row a shift-click extends FROM — the legacy's
  // `uiState.selection.anchorModelId`. It has to survive between clicks, or a
  // range could only ever be drawn once and never adjusted.
  //
  // A ref, not state: nothing renders it, and the legacy writes its anchor
  // synchronously. As state it would still hold the PREVIOUS value for any
  // second click that landed in the same React batch, and the range would be
  // measured from a row the user had already moved off.
  const anchorUid = useRef<string | null>(null)
  /**
   * The last row whose visibility box was ticked — the visibility toggle's own
   * anchor, kept apart from the selection anchor above exactly as the legacy
   * keeps `modelVisibilityToggleAnchorId` apart from its selection anchor.
   */
  const visAnchor = useRef<string | null>(null)
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const dragSource = useRef<string | null>(null)
  // The mark is held in BOTH a ref and state: the ref is what the drop reads,
  // because a `dragover` and the `drop` that follows it can land in the same
  // React batch and the state would still be the previous render's — the drop
  // would then always insert BEFORE. The state is only what draws the line.
  const dropRef = useRef<DropMark | null>(null)
  const [dropMark, setDropMark] = useState<DropMark | null>(null)

  // `revision` alongside `project` in every derivation below: a live edit
  // mutates the project IN PLACE and keeps the same object, so `[project]`
  // alone leaves the tree showing the state before it. The shared liquid
  // modules do exactly that — `addModel()` then `updateUI()` — and a T-branch
  // split this way did not appear in the tree until the next real edit.
  const revision = useSyncExternalStore(store.subscribe, store.revision)
  const total = useMemo(() => (project ? countModels(project) : 0), [project, revision])
  // Resolved once per render rather than per node: a 500-node tree would
  // otherwise do 1500 dictionary lookups for three constant strings.
  const nodeLabels = useMemo<NodeLabels>(
    () => ({
      visibility: t('tooltip.toggle_visibility'),
      duplicate: t('tooltip.duplicate'),
      remove: t('tooltip.delete'),
      // The legacy has no dictionary key for this one: its inspector button is
      // hard-coded English (STL_generate.html:8952). Reusing the string keeps
      // the two apps saying the same thing.
      ungroup: 'Remove from Group',
    }),
    [t],
  )

  const categories = useMemo(
    () => buildTreeCategories(project, groups, sortMode),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [project, revision, sortMode, groups, sceneRev],
  )
  // The rows top to bottom, which is what a shift-click's range is measured in.
  // Derived from the SAME structure the rows are drawn from, so the two cannot
  // disagree about where a model sits.
  const rowOrder = useMemo(() => visualModelOrder(categories), [categories])

  /**
   * Every row of the tree, numbered.
   *
   * The sub-hierarchies come from the cache: building them is ~25,000 objects
   * on a big case, and the layout needs all of them — not just the visible
   * ones — because a row's index depends on how tall every branch above it is.
   */
  const layout = useMemo<TreeLayout>(() => {
    const grouped = new Set<string>()
    for (const g of groups) for (const uid of g.memberIds) grouped.add(uid)
    return buildTreeLayout(categories, fold, {
      childrenOf: (model) =>
        cachedModelChildren(
          model,
          wallRegionNames(model.uid, model.kind === 'floor' ? 'top' : 'front'),
          revision,
        ),
      isGrouped: (uid) => grouped.has(uid),
    })
  }, [categories, fold, groups, revision, locale])

  // --- the visible window ---------------------------------------------------
  const treeRef = useRef<HTMLDivElement>(null)
  const [metrics, setMetrics] = useState<TreeMetrics>(() => ({
    top: 0,
    height: rememberedViewportHeight(),
    rowHeight: ROW_HEIGHT_FALLBACK,
    padTop: PAD_TOP_FALLBACK,
  }))
  const metricsRef = useRef(metrics)
  metricsRef.current = metrics

  useEffect(() => {
    const el = treeRef.current
    if (!el) return
    let frame = 0
    const commit = (next: TreeMetrics) => {
      setMetrics((prev) =>
        prev.top === next.top &&
        prev.height === next.height &&
        prev.rowHeight === next.rowHeight &&
        prev.padTop === next.padTop
          ? prev
          : next,
      )
      rememberViewportHeight(next.height)
    }
    const remeasure = () => commit(measureMetrics(el, el.scrollTop))
    // One frame per burst: a wheel scroll fires dozens of events, and each one
    // that re-rendered synchronously would make the tree feel worse than no
    // windowing at all.
    const onScroll = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        commit({ ...metricsRef.current, top: el.scrollTop })
      })
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(remeasure)
    observer?.observe(el)
    remeasure()
    return () => {
      if (frame) cancelAnimationFrame(frame)
      el.removeEventListener('scroll', onScroll)
      observer?.disconnect()
    }
  }, [previewOn])

  const win = useMemo(
    () =>
      rowWindow(
        layout.totalRows,
        metrics.top,
        metrics.height,
        metrics.rowHeight,
        metrics.padTop,
        OVERSCAN_ROWS,
      ),
    [layout, metrics],
  )
  const winRef = useRef(win)
  winRef.current = win
  const layoutRef = useRef(layout)
  layoutRef.current = layout

  /** Put a row on screen by index, then let the caller find it in the DOM. */
  const scrollToRow = useCallback((row: number): boolean => {
    const el = treeRef.current
    if (!el) return false
    const m = metricsRef.current
    if (!(m.height > 0)) return true // nothing is windowed: every row is there
    const top = row * m.rowHeight + m.padTop
    const viewTop = el.scrollTop
    const viewBottom = viewTop + m.height
    let next = viewTop
    if (top < viewTop) next = top - m.padTop
    else if (top + m.rowHeight > viewBottom) next = top + m.rowHeight - m.height
    next = Math.max(0, next)
    if (Math.abs(next - viewTop) < 0.5) return true
    el.scrollTop = next
    setMetrics((prev) => (prev.top === next ? prev : { ...prev, top: next }))
    return false
  }, [])

  // Panels jump to a row by uid or by node key (`nodeJump`, `selectionBridge`).
  // A windowed tree may not have that row in the DOM yet, so the jump scrolls
  // first and the caller retries — see `treeScroll`.
  useEffect(
    () =>
      registerTreeScroll({
        windowed: winRef.current !== null,
        toUid: (uid) => {
          const row = layoutRef.current.modelRow.get(uid)
          return row == null ? false : scrollToRow(row)
        },
        toKey: (key) => {
          const row = findRowForKey(layoutRef.current, key)
          return row == null ? false : scrollToRow(row)
        },
      }),
    // `win` in the deps: `windowed` is a snapshot of it, and a caller that
    // scrolled the tree while it was unwindowed must not be told to retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [scrollToRow, win],
  )

  // Reveal on a viewport pick (`treeReveal`): open the picked model's category
  // and group folder, then scroll its row into view with block 'nearest'.
  // The request only bumps a counter. The layout effect reads the selection
  // from the SAME render, which already holds the pick: the viewport changes
  // the selection and asks for the reveal in one event, so React commits both
  // together.
  const [revealSeq, setRevealSeq] = useState(0)
  useEffect(() => subscribeTreeReveal(() => setRevealSeq((n) => n + 1)), [])
  const pendingReveal = useRef<{ uid: string; waitForFold: boolean; attempts: number } | null>(null)
  useLayoutEffect(() => {
    // A new request only. A selection or fold change on its own must not
    // scroll: a click on a row would move the row out from under the pointer.
    if (!revealSeq || previewOn || !selectedUid) return
    const keys = treeRevealKeys(categories, selectedUid)
    if (!keys) return
    const folded = keys.some((key) => isFolded(fold, key))
    pendingReveal.current = { uid: selectedUid, waitForFold: folded, attempts: 0 }
    // A folded category renders no rows at all, so the row only exists after
    // this re-render. Set from a LAYOUT effect, the update is applied before the
    // browser paints the folded tree.
    if (folded) setFold((prev) => foldKeys(prev, keys, false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revealSeq])
  useLayoutEffect(() => {
    const pending = pendingReveal.current
    if (!pending) return
    if (pending.waitForFold) {
      pending.waitForFold = false
      return
    }
    const el = treeRef.current
    if (!el) {
      pendingReveal.current = null
      return
    }
    const row = layoutRef.current.modelRow.get(pending.uid)
    // Windowed: the row may not be in the DOM until the scroll lands, so this
    // runs again after the re-render the scroll schedules.
    if (winRef.current && row != null && !scrollToRow(row)) {
      pending.attempts += 1
      if (pending.attempts > 4) pendingReveal.current = null
      return
    }
    pendingReveal.current = null
    const rows = el.querySelectorAll<HTMLElement>('.node-label.model-root-label[data-model-id]')
    const target = Array.from(rows).find((node) => node.dataset.modelId === pending.uid)
    target?.scrollIntoView({ block: 'nearest' })
  })

  // Every hook above runs in both modes; the swap happens here, after them.
  if (previewOn) {
    return <StlPreviewTree project={project} selectedUid={selectedUid} onSelect={onSelect} />
  }

  function cycleSort() {
    const next = nextSortMode(sortMode)
    setSortMode(next)
    try {
      localStorage.setItem(SORT_STORAGE_KEY, next)
    } catch {
      /* private mode: the choice just does not persist */
    }
  }

  /**
   * Fold a branch. `open` is the branch's CURRENT state, which the caller knows
   * and this does not: a node with `defaultCollapsed` has no entry in the map
   * until it is first touched, so `!prev[key]` would read it as open.
   */
  function toggle(key: string, open?: boolean, shift = false) {
    // Shift folds the same branch on every selected model — or on every model
    // when nothing is selected, and on every category for a type caret — to
    // the state THIS caret is moving to. See `batchFoldKeys`.
    const keys = batchFoldKeys(
      key,
      shift,
      multi,
      catSel.categories,
      categories.map((c) => c.bucket as string),
      rowOrder,
    )
    if (keys.length > 1) {
      setFold((prev) => foldKeys(prev, keys, open ?? !isFolded(prev, key)))
      return
    }
    setFold((prev) => toggleFold(prev, key, open))
  }

  /**
   * Select several models at once.
   *
   * One store update when the host offers `onSelectMany`, and a plain click
   * followed by additive ones when it does not. Models already selected are
   * skipped in the additive case: a second additive click on them would TOGGLE
   * them off, and "add this group to the selection" must only ever add.
   *
   * `active` is applied LAST, because the host makes the most recent click the
   * active model and that is the one the inspector follows.
   */
  function selectMany(uids: string[], additive: boolean, active?: string) {
    if (!uids.length) return
    const ordered =
      active && uids.includes(active) ? [...uids.filter((uid) => uid !== active), active] : uids
    if (onSelectMany) {
      onSelectMany(ordered, additive, active ?? null)
      return
    }
    if (!additive) {
      onSelect(ordered[0]!, false)
      for (let i = 1; i < ordered.length; i++) onSelect(ordered[i]!, true)
      return
    }
    for (const uid of ordered) if (!multi.has(uid)) onSelect(uid, true)
  }

  /**
   * Nothing selected — the tree's root node, and the blank strip below it.
   *
   * Three things, not one, because the legacy's `setSelection('project')` does
   * all three (STL_generate.html:6290-6296): the models, the category
   * highlight, and every model's move/rotate tool. Dropping only the models
   * left the eight category rows lit under an empty inspector and 32 racks
   * still carrying `moveTool.enabled`, so the next click on any of them put the
   * group's drag arrows straight back.
   */
  function deselectEverything() {
    setCatSel({ categories: [], anchor: null })
    // The shift-click anchor goes with it: a range measured from a row that is
    // no longer selected is a range the user cannot see the start of.
    anchorUid.current = null
    clearTransformTools()
    onSelect(null)
  }

  /**
   * A click on the tree's background, and only on the background.
   *
   * `target === currentTarget` is the legacy's own guard
   * (STL_generate.html:151): a click that bubbled up from a row has already
   * been handled by that row.
   */
  function clickTreeBackground(e: MouseEvent) {
    if (e.target !== e.currentTarget) return
    deselectEverything()
  }

  /**
   * The root row selects EVERY model; with every model already selected it
   * drops the selection, so the same row is also the way back. The blank strip
   * below the tree still deselects on its own.
   */
  function clickRoot() {
    const allUids = rowOrder
    if (!allUids.length) {
      deselectEverything()
      return
    }
    if (rootClickAction(allUids, multi) === 'deselect') {
      deselectEverything()
      return
    }
    selectMany(allUids, false)
  }

  function clickCategory(e: MouseEvent, bucket: string) {
    const present = categories.map((c) => c.bucket as string)
    const next = categoryClick(present, catSel.anchor, bucket, e.shiftKey)
    setCatSel(next)
    const uids = modelsInCategories(project, next.categories)
    // A category click REPLACES the selection even with ctrl held: only shift
    // means anything here, and it extends across categories rather than adding
    // to what a model row had selected (`selectCategory`,
    // STL_generate.html:13167). A group node is the one that adds on ctrl.
    selectMany(uids, false)
    store.logOperation('selectCategory', `${next.categories.join(',')} (${uids.length})`)
  }

  function clickGroup(e: MouseEvent, group: ModelGroup, members: Construction[]) {
    // Members the project no longer has are already gone from `members`; a
    // folder that outlived every model in it is not drawn at all.
    if (!members.length) return
    // Shift adds a folder's models here rather than extending a range — the
    // legacy's `selectGroupNode` treats it exactly like ctrl
    // (STL_generate.html:4594).
    selectMany(
      members.map((m) => m.uid),
      e.ctrlKey || e.metaKey || e.shiftKey,
    )
    store.logOperation('selectGroup', `${group.name} (${members.length})`)
  }

  /**
   * A click on a model row: plain, ctrl (toggle) or shift (range).
   *
   * The anchor is this component's, not the host's — the host offers one click
   * at a time and knows nothing about tree rows. It falls back to the ACTIVE
   * model so a shift-click straight after a viewport selection still extends
   * from it, as the legacy's anchor does (STL_generate.html:12822).
   */
  function clickModelRow(e: MouseEvent, uid: string) {
    const ctrl = e.ctrlKey || e.metaKey
    const shift = e.shiftKey && !ctrl
    const from = anchorUid.current ?? selectedUid
    anchorUid.current = nextAnchor(anchorUid.current, selectedUid, uid, ctrl || shift)
    if (!shift) {
      onSelect(uid, ctrl)
      return
    }
    const range = rangeSelect(rowOrder, from, uid)
    if (!range.length) return
    // Applied with the clicked row LAST so it ends up active — the legacy picks
    // the clicked model as the active one whenever the range still holds it.
    selectMany(range, false, uid)
    store.logOperation('selectModelTreeNode', `range ${range.length}`)
  }

  /**
   * The visibility checkbox: what it toggles, and what stays selected after.
   *
   * Three branches, as in `toggleModelEnabled` (STL_generate.html:12965):
   * the clicked model is part of a multi-selection, so the whole selection
   * follows; shift+click from the last row toggled, so a range follows; or the
   * one row on its own. The batch is ONE store call, hence one undo step.
   *
   * Its anchor is NOT the selection anchor. The legacy keeps
   * `modelVisibilityToggleAnchorId` for this alone, so a shift+click here
   * measures from the last row whose box was ticked rather than from wherever
   * the last selection started.
   */
  function toggleVisibility(e: { shiftKey?: boolean }, uid: string, checked: boolean) {
    let targets = [uid]
    if (multi.has(uid) && multi.size > 1) {
      // In ROW order, not selection order, so the log and the range agree with
      // what is on screen.
      targets = rowOrder.filter((u) => multi.has(u))
    } else if (e?.shiftKey && visAnchor.current && visAnchor.current !== uid) {
      const range = rangeSelect(rowOrder, visAnchor.current, uid)
      if (range.length) targets = range
    }
    visAnchor.current = uid
    store.setEnabledMany(targets, checked)
    if (checked) return
    // A hidden model that stays selected leaves the inspector editing something
    // nobody can see, and the next drag moves it invisibly.
    const hidden = new Set(targets)
    const remaining = [...multi].filter((u) => !hidden.has(u))
    if (remaining.length === multi.size) return
    if (!remaining.length) onSelect(null)
    else selectMany(remaining, false, remaining[remaining.length - 1])
  }

  const dnd: DragHandlers = {
    mark: dropMark,
    start(e: DragEvent, uid: string) {
      // A drag that began on a button or the caret is not a reorder — the
      // legacy refuses it outright (STL_generate.html:13059) rather than
      // letting a mis-grabbed checkbox move a rack.
      if ((e.target as HTMLElement).closest?.('.node-action-btn, .node-visibility-toggle, .node-expander')) {
        e.preventDefault()
        return
      }
      dragSource.current = uid
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', uid)
    },
    over(e: DragEvent, uid: string) {
      const source = dragSource.current
      if (!source || source === uid) return
      // Without preventDefault the browser refuses the drop outright, and the
      // row never becomes a valid target.
      e.preventDefault()
      const rect = e.currentTarget.getBoundingClientRect()
      const position: DropPosition = e.clientY - rect.top >= rect.height / 2 ? 'after' : 'before'
      dropRef.current = { uid, position }
      setDropMark((prev) =>
        prev && prev.uid === uid && prev.position === position ? prev : { uid, position },
      )
    },
    drop(e: DragEvent, uid: string) {
      e.preventDefault()
      const source = dragSource.current
      const mark = dropRef.current
      dragSource.current = null
      dropRef.current = null
      setDropMark(null)
      if (!source) return
      if (!reorderModel(source, uid, mark?.uid === uid ? mark.position : 'before')) return
      // A name sort would re-order the drop away on the very next render, so
      // honouring the drag means switching the view to manual ordering.
      if (sortMode !== 'manual') {
        setSortMode('manual')
        try {
          localStorage.setItem(SORT_STORAGE_KEY, 'manual')
        } catch {
          /* private mode: the choice just does not persist */
        }
        store.logOperation('setTreeSortMode', t('tree.sort_switched_manual'))
      }
    },
    end() {
      dragSource.current = null
      dropRef.current = null
      setDropMark(null)
    },
  }

  const ctx: TreeCtx = {
    win,
    rowHeight: metrics.rowHeight,
    multi,
    selectedUid,
    selectedNode: selectedNode ?? null,
    labels: nodeLabels,
    t,
    editingGroup,
    setEditingGroup,
    catSelCategories: catSel.categories,
    toggle,
    clickCategory,
    clickGroup,
    clickModelRow,
    toggleVisibility,
    duplicate: (uid) => {
      const created = store.duplicateModel(uid)
      if (created) onSelect(created)
    },
    remove: (uid, selected) => {
      store.removeModel(uid)
      if (selected) onSelect(null)
    },
    ungroup: (uid) => groupStore.removeModelFromGroup(uid),
    renameGroup: (id, name) => groupStore.renameModelGroup(id, name),
    clickSub(node, children, open) {
      if (!node.select) {
        // A group header (Equipments, Partitions) selects nothing; clicking it
        // folds instead, which is the only thing it can usefully do.
        if (children.length) toggle(node.key, open)
        return
      }
      // The model is selected either way, so the viewport highlight and the
      // inspector follow even where the narrower selection is not handled.
      onSelect(node.select.uid)
      onSelectNode?.(node.select)
    },
    dnd,
    onSelect,
    onSelectNode,
  }

  return (
    <>
      <div className="header" id="left-geo-header">
        <span className="title" id="tree-header-title">{t('tree.header_title')}</span>
        <span
          className="badge clickable"
          id="tree-sort-badge"
          data-mode={sortMode}
          onClick={cycleSort}
          title={t('tree.sort_tooltip')}
        >
          {t(SORT_LABEL_KEYS[sortMode])}
        </span>
        <span className="badge" id="mode-badge">
          {t('tree.mode_multi', { count: total })}
        </span>
      </div>

      <div id="tree-root-sticky">
        <div
          className={`node-label${selectedUid === null ? ' selected' : ''}`}
          onClick={clickRoot}
        >
          <span className="node-main">
            <span
              className="node-expander"
              title={t('tooltip.toggle_expand')}
              onClick={(e) => {
                // The caret folds WITHOUT selecting, as every other caret does.
                e.stopPropagation()
                setFold((prev) => foldAll(prev, !rootFolded(prev)))
              }}
            >
              {rootFolded(fold) ? '▸' : '▾'}
            </span>
            <Icon name="folder" />
            <span className="name">
              {project ? `${t('tree.root_node')} · ${store.projectDisplayName()}` : t('tree.root_node')}
            </span>
          </span>
          <span className="badge">{total}</span>
        </div>
      </div>

      <div
        className={`content${win ? ' tree-virtual' : ''}`}
        id="tree-container"
        ref={treeRef}
        onClick={clickTreeBackground}
      >
        {!project && (
          <div className="empty">{t('tree.no_project_hint')}</div>
        )}
        {project && categories.length === 0 && <div className="empty">{t('tree.empty_project')}</div>}
        {project && categories.length > 0 && (
          <ul className="tree">
            {layout.categories.map((lc) => (
              <CategoryNode key={lc.cat.bucket} lc={lc} ctx={ctx} />
            ))}
          </ul>
        )}
      </div>
    </>
  )
}

/**
 * The rows a window skips, as one empty `li` of the right height.
 *
 * The pitch is fixed by `styles.css` under `.tree-virtual` — that is what makes
 * a row count convertible to pixels at all.
 */
function Spacer({ rows, rowHeight }: { rows: number; rowHeight: number }) {
  if (rows <= 0) return null
  return (
    <li
      className="tree-node tree-spacer"
      aria-hidden="true"
      style={{ height: rows * rowHeight }}
    />
  )
}

/** One level of entries, windowed: spacer, the rows on screen, spacer. */
function renderEntries(entries: readonly LayoutEntry[], ctx: TreeCtx) {
  const slice = windowSlice(entries, ctx.win)
  return (
    <>
      <Spacer rows={slice.before} rowHeight={ctx.rowHeight} />
      {slice.shown.map((entry) =>
        entry.kind === 'group' ? (
          <GroupNode key={entry.group.id} le={entry} ctx={ctx} />
        ) : (
          <ModelNode key={entry.model.uid} le={entry} ctx={ctx} />
        ),
      )}
      <Spacer rows={slice.after} rowHeight={ctx.rowHeight} />
    </>
  )
}

function CategoryNode({ lc, ctx }: { lc: LayoutCategory; ctx: TreeCtx }) {
  const key = `cat:${lc.cat.bucket}`
  const anySelected =
    lc.cat.groups.some((g) => g.members.some((m) => ctx.multi.has(m.uid))) ||
    lc.cat.models.some((m) => ctx.multi.has(m.uid))
  return (
    <li className="tree-node">
      <div
        className={`node-label${
          anySelected || ctx.catSelCategories.includes(lc.cat.bucket) ? ' selected' : ''
        }`}
        data-category={lc.cat.bucket}
        onClick={(e) => ctx.clickCategory(e, lc.cat.bucket)}
      >
        <span className="node-main">
          <span
            className="node-expander"
            onClick={(e) => {
              // The caret folds WITHOUT selecting: opening a category to look
              // inside it must not select all 142 racks in it.
              e.stopPropagation()
              ctx.toggle(key, undefined, e.shiftKey)
            }}
          >
            {lc.open ? '▾' : '▸'}
          </span>
          <Glyph icon={lc.cat.icon} />
          <span className="name">{ctx.t(lc.cat.labelKey)}</span>
        </span>
        <span className="badge">{lc.cat.count}</span>
      </div>
      {lc.open && (
        <ul data-tree-children={key}>{renderEntries(lc.entries, ctx)}</ul>
      )}
    </li>
  )
}

function GroupNode({ le, ctx }: { le: LayoutGroup; ctx: TreeCtx }) {
  const gKey = `group:${le.group.id}`
  const selected = le.members.some((m) => ctx.multi.has(m.model.uid))
  return (
    <li className="tree-node">
      <div
        className={`node-label${selected ? ' selected' : ''}`}
        data-group={le.group.id}
        onClick={(e) => ctx.clickGroup(e, le.group, le.members.map((m) => m.model))}
      >
        <span className="node-main">
          <span
            className="node-expander"
            onClick={(e) => {
              e.stopPropagation()
              ctx.toggle(gKey)
            }}
          >
            {le.open ? '▾' : '▸'}
          </span>
          <Icon name="folder" />
          {ctx.editingGroup === le.group.id ? (
            <input
              className="group-name-input"
              id="group-name-input"
              autoFocus
              defaultValue={le.group.name}
              onClick={(e) => e.stopPropagation()}
              onBlur={(e) => {
                const name = e.target.value.trim()
                // An empty name would leave a folder with no way to tell it
                // from the next one.
                if (name) ctx.renameGroup(le.group.id, name)
                ctx.setEditingGroup(null)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
                if (e.key === 'Escape') {
                  e.currentTarget.value = le.group.name
                  e.currentTarget.blur()
                }
              }}
            />
          ) : (
            <span
              className="name"
              title={ctx.t('label.group_name_prefix', { name: le.group.name })}
              onDoubleClick={(e) => {
                e.stopPropagation()
                ctx.setEditingGroup(le.group.id)
              }}
            >
              {le.group.name}
            </span>
          )}
        </span>
        <span className="badge">{le.members.length}</span>
      </div>
      {le.open && <ul>{renderEntries(le.members, ctx)}</ul>}
    </li>
  )
}

function ModelNode({ le, ctx }: { le: LayoutModel; ctx: TreeCtx }) {
  const model = le.model
  const selected = ctx.multi.has(model.uid)
  const active = model.uid === ctx.selectedUid
  // `enabled` is false on plenty of real models; the legacy tree greys those out
  // rather than hiding them, so a disabled object is still selectable.
  const enabled = model.meta?.enabled !== false
  const key = `model:${model.uid}`
  const mark = ctx.dnd.mark?.uid === model.uid ? ctx.dnd.mark.position : null
  // The narrow node goes to the ACTIVE model's rows only, and only for a
  // single selection: a multi-selection has no narrow node in the legacy
  // either, and a node left over from another model must light nothing.
  const node = active && ctx.multi.size <= 1 ? ctx.selectedNode : null
  return (
    <li className="tree-node">
      <div
        className={
          `node-label model-root-label${selected ? ' selected' : ''}` +
          `${selected && !active ? ' co-selected' : ''}${enabled ? '' : ' disabled'}` +
          `${mark ? ` drop-${mark}` : ''}`
        }
        data-model-id={model.uid}
        draggable
        onDragStart={(e) => ctx.dnd.start(e, model.uid)}
        onDragOver={(e) => ctx.dnd.over(e, model.uid)}
        onDrop={(e) => ctx.dnd.drop(e, model.uid)}
        onDragEnd={ctx.dnd.end}
        // The drop line is drawn inline rather than from styles.css: the legacy
        // toggles these two classes and styles neither, so a ported class name
        // alone would leave a drag with no feedback at all.
        style={
          mark
            ? { [mark === 'before' ? 'borderTop' : 'borderBottom']: '2px solid var(--accent3, #00d4ff)' }
            : undefined
        }
        onClick={(e) => {
          e.stopPropagation()
          ctx.clickModelRow(e, model.uid)
        }}
        title={model.uid}
      >
        <span className="node-main">
          {le.children.length > 0 && (
            <span
              className="node-expander"
              onClick={(e) => {
                // The caret toggles WITHOUT selecting: opening a rack to look
                // at its servers should not change what the inspector shows.
                e.stopPropagation()
                ctx.toggle(key, undefined, e.shiftKey)
              }}
            >
              {le.open ? '▾' : '▸'}
            </span>
          )}
          <input
            type="checkbox"
            className="node-visibility-toggle"
            checked={enabled}
            onClick={(e) => e.stopPropagation()}
            // The modifier keys ride on the CLICK event React derives this
            // from; the synthetic ChangeEvent carries none of them.
            onChange={(e) =>
              ctx.toggleVisibility(e.nativeEvent as { shiftKey?: boolean }, model.uid, e.target.checked)
            }
            title={ctx.labels.visibility}
          />
          <Glyph icon={le.icon} />
          <span className="name">{model.meta?.name ?? model.uid}</span>
        </span>
        <span className="node-actions">
          {le.grouped && (
            <button
              className="node-action-btn"
              title={ctx.labels.ungroup}
              onClick={(e) => {
                e.stopPropagation()
                ctx.ungroup(model.uid)
              }}
            >
              ⏏
            </button>
          )}
          <button
            className="node-action-btn"
            title={ctx.labels.duplicate}
            onClick={(e) => {
              e.stopPropagation()
              ctx.duplicate(model.uid)
            }}
          >
            <Icon name="copy" size={12} />
          </button>
          <button
            className="node-action-btn"
            title={ctx.labels.remove}
            onClick={(e) => {
              e.stopPropagation()
              ctx.remove(model.uid, selected)
            }}
          >
            ✕
          </button>
        </span>
      </div>
      {le.open && le.children.length > 0 && (
        <ul>{renderChildren(le.subtree, node, ctx)}</ul>
      )}
    </li>
  )
}

/**
 * A node below a model: a face, a patch, a server, a partition, a group header.
 *
 * One component for every depth, because the legacy tree draws them the same
 * way — an expander when there are children, an optional colour dot, a label
 * and an optional badge — and the differences between them live in the data
 * `modelChildren` produced, not in the rendering.
 */
function renderChildren(list: readonly LayoutChild[], current: TreeSelection | null, ctx: TreeCtx) {
  const slice = windowSlice(list, ctx.win)
  return (
    <>
      <Spacer rows={slice.before} rowHeight={ctx.rowHeight} />
      {slice.shown.map((lc) => (
        <SubNode key={lc.node.key} lc={lc} current={current} ctx={ctx} />
      ))}
      <Spacer rows={slice.after} rowHeight={ctx.rowHeight} />
    </>
  )
}

function SubNode({
  lc,
  current,
  ctx,
}: {
  lc: LayoutChild
  /** The active model's narrow selection, or null for every other model. */
  current: TreeSelection | null
  ctx: TreeCtx
}) {
  const node = lc.node
  const children = node.children ?? []
  return (
    <li className="tree-node">
      <div
        className={`node-label${isTreeNodeHighlighted(node.select, current, children) ? ' selected' : ''}`}
        data-node={node.key}
        onClick={(e) => {
          e.stopPropagation()
          ctx.clickSub(node, children, lc.open)
        }}
      >
        <span className="node-main">
          {children.length > 0 && (
            <span
              className="node-expander"
              onClick={(e) => {
                e.stopPropagation()
                ctx.toggle(node.key, lc.open, e.shiftKey)
              }}
            >
              {lc.open ? '▾' : '▸'}
            </span>
          )}
          {node.dot && <span className="node-dot" style={{ background: node.dot }} />}
          {/* The door row's visibility box. `stopPropagation` on the click, or
              ticking it would also select the row underneath. */}
          {node.checkbox && (
            <input
              type="checkbox"
              className="node-visibility-toggle"
              checked={node.checkbox.checked}
              title={node.checkbox.title}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => node.checkbox?.onChange(e.target.checked)}
            />
          )}
          <span className="name">{node.label}</span>
        </span>
        {node.badge && <span className="badge">{node.badge}</span>}
        {node.action && (
          <span className="node-actions">
            <button
              className="node-action-btn"
              title={node.action.title}
              onClick={(e) => {
                e.stopPropagation()
                node.action?.onClick()
              }}
            >
              {node.action.icon}
            </button>
          </span>
        )}
      </div>
      {lc.open && children.length > 0 && (
        <ul>{renderChildren(lc.children, current, ctx)}</ul>
      )}
    </li>
  )
}
