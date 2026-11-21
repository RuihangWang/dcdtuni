import { useEffect, useMemo, useRef, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import { stateV4 } from '../legacy/cfdprojModules'
import { patchAtTriangle } from '../geometry/patchHighlight'
import { usePatchListControls } from '../geometry/patchListControls'
import { registerPatchPickSource } from '../geometry/patchViewportPick'
import {
  clearPpHighlight,
  ppSurfaceMesh,
  updatePpHighlight,
} from '../postprocess/ppPatchHighlight'
import {
  KIND_COLORS,
  KIND_LABELS,
  groupPatchesByModelKind,
  isFlatKind,
  kindPatchCount,
  orderedKinds,
  patchGroupLabel,
  type ModelLike,
  type PatchLike,
} from '../postprocess/ppPatchTree'
import {
  getPpView,
  setPpInternalVisible,
  setPpPatchesVisible,
  setPpReadZones,
  subscribePpView,
} from '../postprocess/ppViewState'
import { isLiquidModeActive, subscribeLiquidMode } from '../liquid/liquidModeState'
import { RENDER_ORDER_DEFAULTS } from '../ui/renderOrder'
import { requestPpClearAll, subscribePpClearAll } from '../postprocess/ppClearAll'
import { useTranslation } from '../useTranslation'
import { LiquidPostprocessRightPanel } from './LiquidPostprocessRightPanel'
import { RenderOrderChips } from './RenderOrderChips'

/**
 * The right-hand panel on the post-processing tab.
 *
 * Replicates `#dctwin-postprocess-right` (STL_generate.html:928): display
 * options above, the patch list below. On this tab the legacy editor HIDES the
 * creation toolbar and the inspector and shows this instead, which is why it is
 * a panel rather than another section — the right column is a different thing
 * here, not the same thing with an extra group.
 *
 * ## Why the sliders talk to the viewport directly
 *
 * Opacity, lighting and render order are properties of Three.js objects, not of
 * the project or of the render request. Routing them through the left panel's
 * render cycle would refetch a 5.7 MB mesh to change a material's alpha.
 *
 * The patch checkboxes are the exception: this app draws all 53 patches as ONE
 * geometry with a shared vertex-colour array, so hiding one is an index
 * rebuild, and the left panel — which owns the decoded mesh — has to do it.
 * That is why the hidden set goes through the shared store.
 *
 * ## Selection is not visibility
 *
 * The checkbox says whether a patch is DRAWN; the row highlight says which
 * patch you are looking at. They are separate on purpose — the legacy pair
 * `patch_selection.js` + `ppHighlightPatch` works the same way — because the
 * question "which of these 53 names is the rack inlet I just clicked in the
 * viewport" cannot be answered by hiding the other 52.
 */

interface PpViewportApi {
  clear(): void
  setModelOpacity(v: number): void
  setFieldOpacity(v: number): void
  setLightIntensity(v: number): void
  setAO(on: boolean): boolean
  setAOStrength(v: number): void
  setInternalVisible(v: boolean): void
  setRenderOrder(keys: string[]): void
}

function viewport(): PpViewportApi | null {
  return (window as unknown as { __ppViewport?: PpViewportApi }).__ppViewport ?? null
}

const DEFAULT_ORDER = RENDER_ORDER_DEFAULTS.postprocess as string[]

export interface PostprocessRightPanelProps {
  project: CfdProject | null
}

/**
 * The post-processing right column, for whichever cooling mode is on.
 *
 * Two panels, not one with branches: an air result is a FIELD on a mesh, so the
 * column is opacity, lighting and a patch list; a liquid result is a network
 * solved out of a `.liquidrun`, with no surface to shade and no patches — the
 * column there is which solve to read. `_applyLeftTabUI` picks between the same
 * two elements (STL_generate.html:19369-19373), and it also hides the inspector
 * on this tab in BOTH modes, which is why the liquid half belongs here rather
 * than beside the property panel.
 */
export function PostprocessRightPanel(props: PostprocessRightPanelProps) {
  const [, bump] = useState(0)
  useEffect(() => subscribeLiquidMode(() => bump((n) => n + 1)), [])
  if (isLiquidModeActive()) return <LiquidPostprocessRightPanel />
  return <AirPostprocessRightPanel {...props} />
}

function AirPostprocessRightPanel({ project }: PostprocessRightPanelProps) {
  const { t } = useTranslation()
  const [, forceRender] = useState(0)
  useEffect(() => subscribePpView(() => forceRender((n) => n + 1)), [])
  const view = getPpView()

  // The legacy slider's own default is 15%: on this tab the geometry is a
  // backdrop for the result, not the subject.
  const [modelOpacity, setModelOpacity] = useState(0.15)
  // `_restoreOriginalGeometry()` at the end of the legacy's `ppClearAll`
  // (STL_generate.html:24232). Post-processing dims the geometry to 15% so the
  // field shows through it; leaving it there after a clear is a room the user
  // can barely see with nothing in front of it to explain why.
  useEffect(
    () =>
      subscribePpClearAll(() => {
        setModelOpacity(1)
        viewport()?.setModelOpacity(1)
      }),
    [],
  )
  const [fieldOpacity, setFieldOpacity] = useState(1)
  const [lightIntensity, setLightIntensity] = useState(1)
  const [ao, setAO] = useState(false)
  const [aoStrength, setAOStrength] = useState(2)
  const [order, setOrder] = useState<string[]>(DEFAULT_ORDER)
  // Absent keys are folded, including groups first seen on a later refresh.
  // Preserve the user's expansion choices while reloading the same result.
  const [collapseState, setCollapseState] = useState<{
    runDir: string | null
    values: Record<string, boolean>
  }>({ runDir: view.runDir, values: {} })
  const collapsed = collapseState.runDir === view.runDir ? collapseState.values : {}
  function setCollapsed(update: (previous: Record<string, boolean>) => Record<string, boolean>) {
    setCollapseState((previous) => ({
      runDir: view.runDir,
      values: update(previous.runDir === view.runDir ? previous.values : {}),
    }))
  }

  // Push the defaults into the viewport when the tab opens. The legacy editor
  // does the same on `_roShowPanel('postprocess')` — a panel showing 15% while
  // the scene renders opaque is worse than either value.
  useEffect(() => {
    const vp = viewport()
    if (!vp) return
    vp.setModelOpacity(modelOpacity)
    vp.setRenderOrder(order)
    // Intentionally once, on mount: later changes go through the handlers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const models: ModelLike[] = useMemo(() => {
    if (!project) return []
    return stateV4().allModels(project) as ModelLike[]
  }, [project])

  const groups = useMemo(
    () => groupPatchesByModelKind(view.patches as PatchLike[], models),
    [view.patches, models],
  )
  const kinds = orderedKinds(groups)
  const allNames = useMemo(() => view.patches.map((p) => p.name), [view.patches])

  // ── selection: rows, and the purple overlay on the merged surface ─────────
  const selectedRef = useRef<Set<string>>(new Set())
  const viewRef = useRef(view)
  viewRef.current = view

  // The overlay is redrawn OUTSIDE the render cycle. A selection change moves
  // no React state — the rows take their `row-selected` class imperatively —
  // so waiting for a re-render would leave the viewport unmarked until some
  // unrelated control moved. The signature is what keeps that cheap: selecting
  // a whole kind calls `highlight` once per patch, and each call schedules the
  // same single rebuild. It also catches the change the selection cannot see,
  // the surface being rebuilt under the overlay, which invalidates the index
  // ranges the overlay was sliced from.
  const overlaySig = useRef('')
  const overlayPending = useRef(false)
  const syncOverlay = () => {
    const v = viewRef.current
    const sig = [
      [...selectedRef.current].sort().join(' '),
      v.hidden.size,
      v.patches.length,
      ppSurfaceMesh()?.geometry.uuid ?? '',
    ].join('|')
    if (sig === overlaySig.current) return
    overlaySig.current = sig
    updatePpHighlight(selectedRef.current, v.patches, v.hidden)
  }
  const scheduleOverlay = () => {
    if (overlayPending.current) return
    overlayPending.current = true
    queueMicrotask(() => {
      overlayPending.current = false
      syncOverlay()
    })
  }

  const controls = usePatchListControls({
    names: allNames,
    highlight: (name, on) => {
      const newlySelected = on && !selectedRef.current.has(name)
      if (on) selectedRef.current.add(name)
      else selectedRef.current.delete(name)
      if (newlySelected) {
        for (const [kind, byModel] of Object.entries(groups)) {
          for (const [model, patches] of Object.entries(byModel)) {
            if (patches.some((p) => p.name === name)) {
              setCollapsed((c) => ({ ...c, [kind]: false, [`${kind}/${model}`]: false }))
            }
          }
        }
      }
      scheduleOverlay()
    },
  })

  useEffect(() => {
    controls.selectionRef.current?.clear()
    setCollapseState({ runDir: view.runDir, values: {} })
  }, [view.runDir, controls])

  useEffect(syncOverlay)
  useEffect(() => () => clearPpHighlight(), [])

  // Clicking the field in the viewport reveals which patch it is. The surface
  // is one mesh, so the hit has to be resolved through the triangle ranges.
  useEffect(
    () =>
      registerPatchPickSource({
        // Below the mesh preview: when both are up the preview is what is drawn
        // on top, so it is what the click is aimed at.
        priority: 0,
        active: () => !!ppSurfaceMesh(),
        meshes: () => {
          const m = ppSurfaceMesh()
          return m ? [m] : []
        },
        resolve: (hit) =>
          patchAtTriangle(viewRef.current.patches, viewRef.current.hidden, hit.faceIndex ?? -1),
        container: () => controls.containerRef.current,
        model: () => controls.selectionRef.current,
      }),
    [controls],
  )

  function patchRow(p: PatchLike) {
    const visible = !view.hidden.has(p.name)
    return (
      <div
        className="pp-patch-row"
        data-patch={p.name}
        key={p.name}
        onClick={(e) => controls.onRowClick(p.name, e)}
      >
        <input
          type="checkbox"
          className="pp-patch-cb"
          data-patch={p.name}
          checked={visible}
          onChange={(e) => {
            const { targets, visible: next } = controls.checkboxTargets(p.name, e)
            setPpPatchesVisible(targets, next)
          }}
        />
        <span>
          {p.name} <span className="dim">({p.nTriangles})</span>
        </span>
      </div>
    )
  }

  return (
    <div id="dctwin-postprocess-right">
      {/* ── display options ─────────────────────────────────────────────── */}
      <div className="pp-display-options">
        <div className="pp-section-label">{t('pp.display_options')}</div>

        <div className="pp-slider-row">
          <label htmlFor="pp-model-opacity">{t('pp.model_opacity')}</label>
          <span id="pp-model-opacity-val">{Math.round(modelOpacity * 100)}%</span>
        </div>
        <input
          type="range"
          id="pp-model-opacity"
          min={0}
          max={1}
          step={0.05}
          value={modelOpacity}
          onChange={(e) => {
            const v = Number(e.target.value)
            setModelOpacity(v)
            viewport()?.setModelOpacity(v)
          }}
        />

        <div className="pp-slider-row">
          <label htmlFor="pp-ao-enable" title="Eye-Dome Lighting depth cue on solid geometry">
            {t('pp.ao')}
          </label>
          <input
            type="checkbox"
            id="pp-ao-enable"
            checked={ao}
            onChange={(e) => {
              setAO(e.target.checked)
              viewport()?.setAO(e.target.checked)
              // The pass is built lazily on first render; push the strength in
              // so turning AO on does not start at the shader's own default.
              if (e.target.checked) viewport()?.setAOStrength(aoStrength)
            }}
          />
        </div>
        <div className="pp-slider-row">
          <label htmlFor="pp-ao-strength" className="dim">
            {t('pp.ao_strength')}
          </label>
          <span id="pp-ao-strength-val">{aoStrength.toFixed(1)}</span>
        </div>
        <input
          type="range"
          id="pp-ao-strength"
          min={0}
          max={6}
          step={0.25}
          value={aoStrength}
          onChange={(e) => {
            const v = Number(e.target.value)
            setAOStrength(v)
            viewport()?.setAOStrength(v)
          }}
        />

        <div className="pp-slider-row">
          <label htmlFor="pp-field-opacity" style={{ color: '#8be9fd' }}>
            {t('pp.field_opacity')}
          </label>
          <span id="pp-field-opacity-val">{Math.round(fieldOpacity * 100)}%</span>
        </div>
        <input
          type="range"
          id="pp-field-opacity"
          min={0}
          max={1}
          step={0.05}
          value={fieldOpacity}
          onChange={(e) => {
            const v = Number(e.target.value)
            setFieldOpacity(v)
            viewport()?.setFieldOpacity(v)
          }}
        />

        <div className="pp-slider-row">
          <label htmlFor="pp-light-intensity">{t('pp.light_intensity')}</label>
          <span id="pp-light-intensity-val">{Math.round(lightIntensity * 100)}%</span>
        </div>
        <input
          type="range"
          id="pp-light-intensity"
          min={0}
          max={2}
          step={0.05}
          value={lightIntensity}
          onChange={(e) => {
            const v = Number(e.target.value)
            setLightIntensity(v)
            viewport()?.setLightIntensity(v)
          }}
        />

        <button
          id="pp-clear-all"
          className="btn danger"
          onClick={() => {
            // The scene, then the session. Emptying the group alone left the
            // slice and streamline lists in the other panel intact — a list of
            // sets with nothing drawn — and the geometry still dimmed to the
            // 15% that post-processing puts it at.
            viewport()?.clear()
            requestPpClearAll()
          }}
        >
          {t('pp.clear_all_render')}
        </button>

        <RenderOrderChips
          panelId="pp-render-order-panel"
          order={order}
          onReorder={(next) => {
            setOrder(next)
            viewport()?.setRenderOrder(next)
          }}
        />
      </div>

      {/* ── patch list ──────────────────────────────────────────────────── */}
      <div className="header">{t('pp.patch_list')}</div>

      <div className="pp-zones-row">
        <label>
          <input
            type="checkbox"
            id="pp-read-zones-new"
            checked={view.readZones}
            onChange={(e) => setPpReadZones(e.target.checked)}
          />{' '}
          Read Zones
        </label>
      </div>

      <div className="pp-tree-wrap">
        <div className="pp-tree-head">
          <label>MultiBlock Inspector</label>
          <div className="pp-tree-head-btns">
            {/* These two work here. In the legacy editor they query
                '#pp-tree-boundary', an id that element never gets, so both
                buttons are dead there — see docs/reference/pp-patch-list.md. */}
            {/* `onScreen` on both: with a filter active these mean the rows
                the filter left, the same rule as every other group gesture. */}
            <button
              id="pp-select-all"
              onClick={() => setPpPatchesVisible(controls.onScreen(allNames), true)}
            >
              {t('common.select_all')}
            </button>
            <button
              id="pp-deselect-all"
              onClick={() => setPpPatchesVisible(controls.onScreen(allNames), false)}
            >
              {t('common.deselect_all')}
            </button>
          </div>
        </div>

        <div id="pp-tree" ref={controls.containerRef}>
          {/* The filter bar builds its own DOM into this empty host. React
              renders nothing inside it, so the bar — and the text and toggles
              the user set on it — survives every rebuild of the rows below. */}
          <div className="patch-filter-host" ref={controls.filterHostRef} />

          {kinds.map((kind) => {
            const byModel = groups[kind] ?? {}
            const modelNames = Object.keys(byModel).sort()
            const flat = isFlatKind(byModel)
            const kindNames = modelNames.flatMap((m) => (byModel[m] ?? []).map((p) => p.name))
            const allOn = controls.onScreen(kindNames).every((n) => !view.hidden.has(n))
            const open = collapsed[kind] === false
            return (
              <div className="pp-kind" key={kind}>
                <div
                  className="pp-kind-head"
                  style={{ color: KIND_COLORS[kind] ?? '#999' }}
                >
                  <span
                    className="pp-caret"
                    onClick={() => setCollapsed((c) => ({ ...c, [kind]: c[kind] === false }))}
                  >
                    {open ? '▼' : '▶'}
                  </span>
                  <input
                    type="checkbox"
                    className="patch-group-cb"
                    data-kind={kind}
                    checked={allOn}
                    onChange={(e) =>
                      setPpPatchesVisible(controls.onScreen(kindNames), e.target.checked)
                    }
                  />
                  {KIND_LABELS[kind] ?? 'OTHER'}{' '}
                  <span className="dim">({kindPatchCount(groups, kind)})</span>
                </div>
                {/* Keep folded rows available to filtering, group toggles and viewport picking. */}
                <div className="pp-kind-body" style={{ display: open ? '' : 'none' }}>
                  {flat
                    ? (byModel[modelNames[0] as string] ?? []).map(patchRow)
                    : modelNames.map((mn) => {
                        const list = byModel[mn] ?? []
                        const names = list.map((p) => p.name)
                        const mOpen = collapsed[`${kind}/${mn}`] === false
                        return (
                          <div key={mn}>
                            <div className="pp-model-head">
                              <span
                                className="pp-caret"
                                onClick={() =>
                                  setCollapsed((c) => ({
                                    ...c,
                                    [`${kind}/${mn}`]: c[`${kind}/${mn}`] === false,
                                  }))
                                }
                              >
                                {mOpen ? '▼' : '▶'}
                              </span>
                              <input
                                type="checkbox"
                                className="patch-model-cb"
                                data-model={mn}
                                checked={controls
                                  .onScreen(names)
                                  .every((n) => !view.hidden.has(n))}
                                onChange={(e) =>
                                  setPpPatchesVisible(controls.onScreen(names), e.target.checked)
                                }
                              />
                              {patchGroupLabel(mn, t('mp.domain_boundary'))}{' '}
                              <span className="dim">({list.length})</span>
                            </div>
                            <div className="pp-model-body" style={{ display: mOpen ? '' : 'none' }}>
                              {list.map(patchRow)}
                            </div>
                          </div>
                        )
                      })}
                </div>
              </div>
            )
          })}

          <div className="pp-internal-row">
            ▶{' '}
            <input
              type="checkbox"
              id="pp-internal-cb"
              checked={view.internalVisible}
              onChange={(e) => {
                setPpInternalVisible(e.target.checked)
                viewport()?.setInternalVisible(e.target.checked)
              }}
            />{' '}
            <b>internalMesh</b>
          </div>

          {/* Zones stay dimmed and inert until Read Zones is on — cellZones are
              not read from the result otherwise, so the rows would be empty. */}
          <div id="pp-tree-zones" className={view.readZones ? '' : 'disabled'}>
            <div>
              ▶ <b>zones</b> <span className="dim">{t('pp.enable_read_zones')}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
