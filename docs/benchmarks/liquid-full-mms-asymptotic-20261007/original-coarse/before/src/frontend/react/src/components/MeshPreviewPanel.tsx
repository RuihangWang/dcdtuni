import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { setEdgeHighlight, setPatchHighlight } from '../geometry/patchHighlight'
import { usePatchListControls } from '../geometry/patchListControls'
import { registerPatchPickSource } from '../geometry/patchViewportPick'
import {
  KIND_COLORS,
  KIND_LABELS,
  groupPatchesByModelKind,
  isFlatKind,
  kindPatchCount,
  missingEnclosurePatches,
  orderedKinds,
  patchGroupLabel,
  serverSubgroups,
  type PatchLike,
} from '../postprocess/ppPatchTree'
import {
  abortMeshPreviewLoad,
  closeMeshPreviewSession,
  getMeshPreviewSession,
  loadMeshPreviewSession,
  setMeshPreviewCollapsed,
  setMeshPreviewGeoOpacity,
  setMeshPreviewOpacity,
  setMeshPreviewOrder,
  setMeshPreviewPatchesVisible,
  subscribeMeshPreview,
} from '../run/meshPreviewSession'
import { mergedPatchAtTriangle } from '../run/meshPreview'
import { useTranslation } from '../useTranslation'
import { PpInitTask } from './PpInitTask'
import { RenderOrderChips } from './RenderOrderChips'

/**
 * The mesh preview panel — the right column while the mesh tab shows a mesh.
 *
 * A port of `#mesh-preview-right` (STL_generate.html:998). The patch list is
 * the working part: after a mesh run, the question is which declared regions
 * actually became boundary patches, and this is where you see one missing.
 *
 * ## The list is the same list as the post-processing one
 *
 * The legacy builds both through `_buildGroupedPatchTree`, and both get the
 * same filter bar and the same selection model. A mesh with 400 patches is
 * exactly the case where a flat list stops answering the question — so the tree
 * here is `groupPatchesByModelKind`, the same grouping the post-processing
 * panel uses, over the same rows carrying `data-patch`.
 *
 * ## This component owns nothing
 *
 * The mesh, the patch table, the hidden set, the sliders and the fold state all
 * live in `meshPreviewSession`, and this is a view over them. It used to hold
 * them in refs and dispose them on unmount, which meant that switching to the
 * geometry tab to look at a solid threw the decoded mesh away and coming back
 * re-downloaded it. Leaving the tab now hides the group at the Three.js level;
 * only 关闭预览 and loading another project end the preview.
 */

export function MeshPreviewPanel() {
  const { t } = useTranslation()
  const session = useSyncExternalStore(subscribeMeshPreview, getMeshPreviewSession)
  const { patches, models, hidden, meshOpacity, geoOpacity, order, collapsed, busy, error, loadLines, summary } =
    session

  const names = useMemo(() => patches.map((p) => p.name), [patches])
  const groups = useMemo(
    () => groupPatchesByModelKind(patches as PatchLike[], models),
    [patches, models],
  )
  const kinds = orderedKinds(groups)
  // The room walls, floor and ceiling a leaked mesh lost. Against the models
  // snapshotted at load, like the grouping.
  const missingEnclosure = useMemo(
    () => missingEnclosurePatches(patches as PatchLike[], models),
    [patches, models],
  )

  const kindsRef = useRef(kinds)
  kindsRef.current = kinds
  const groupsRef = useRef(groups)
  groupsRef.current = groups

  const controls = usePatchListControls({
    names,
    // Per-patch meshes here, unlike the post-processing surface: highlighting
    // is a material swap on the patch's own surface and its wireframe.
    highlight: (name, on) => {
      const built = getMeshPreviewSession().built
      const entry = built?.byPatch.get(name)
      if (!entry) return
      // In merged mode the per-patch meshes are built but NOT parented — the
      // single merged surface is what draws. Painting an off-scene material is
      // invisible, which is why the purple highlight the HTML editor shows was
      // missing here: it temporarily ADDS the selected patch's own surface and
      // edges to the group (they sit exactly on the merged surface and, being
      // added later, paint over it), and takes them out again on un-highlight.
      // Same semantics as mpHighlightPatch (STL_generate.html:25804).
      if (built!.merged) {
        // The merged surface writes depth in the very plane the overlay sits
        // in, and both carry polygonOffset +1, so the added overlay LOST the
        // depth test and the purple never showed — worst on sheets (baffle,
        // floor, wall), where the overlay is the whole visible patch and the
        // user sees nothing at all. Pull the overlay toward the camera, stop
        // it writing depth, and draw it after the merged pair; the outline
        // skips the depth test entirely so it reads at any angle.
        const sm = entry.surface.material as {
          polygonOffsetFactor: number
          polygonOffsetUnits: number
          depthWrite: boolean
          opacity: number
          userData: Record<string, unknown>
        }
        const em = entry.edges.material as {
          depthTest: boolean
          opacity: number
          userData: Record<string, unknown>
        }
        if (on) {
          if (!entry.surface.parent) built!.group.add(entry.surface)
          if (!entry.edges.parent) built!.group.add(entry.edges)
          entry.surface.visible = true
          entry.edges.visible = true
          if (sm.userData._hlOrig === undefined) {
            sm.userData._hlOrig = {
              f: sm.polygonOffsetFactor,
              u: sm.polygonOffsetUnits,
              w: sm.depthWrite,
              o: sm.opacity,
              r: entry.surface.renderOrder,
            }
          }
          sm.polygonOffsetFactor = -1
          sm.polygonOffsetUnits = -1
          sm.depthWrite = false
          sm.opacity = 1
          entry.surface.renderOrder = 102
          if (em.userData._hlOrig === undefined) {
            em.userData._hlOrig = {
              d: em.depthTest,
              o: em.opacity,
              r: entry.edges.renderOrder,
            }
          }
          em.depthTest = false
          em.opacity = 1
          entry.edges.renderOrder = 103
        } else {
          const so = sm.userData._hlOrig as
            | { f: number; u: number; w: boolean; o: number; r: number }
            | undefined
          const eo = em.userData._hlOrig as
            | { d: boolean; o: number; r: number }
            | undefined
          if (so) {
            sm.polygonOffsetFactor = so.f
            sm.polygonOffsetUnits = so.u
            sm.depthWrite = so.w
            sm.opacity = so.o
            entry.surface.renderOrder = so.r
          }
          if (eo) {
            em.depthTest = eo.d
            em.opacity = eo.o
            entry.edges.renderOrder = eo.r
          }
          entry.surface.visible = false
          entry.edges.visible = false
          if (entry.surface.parent) built!.group.remove(entry.surface)
          if (entry.edges.parent) built!.group.remove(entry.edges)
        }
      }
      setPatchHighlight(entry.surface, on)
      setEdgeHighlight(entry.edges, on)
      if (!on) return
      // Unfold the groups the selected patch sits in, so its row (and the
      // heads above it) can show the selection. Only when something has to
      // change: this runs again after every rebuild for the whole selection.
      for (const kind of kindsRef.current) {
        const byModel = groupsRef.current[kind] ?? {}
        for (const mn of Object.keys(byModel)) {
          if (!(byModel[mn] ?? []).some((p) => p.name === name)) continue
          const mk = `${kind}/${mn}`
          // A rack's rows sit one level deeper, per server; that fold has to
          // open too or the selected row stays out of sight.
          const sk =
            kind === 'rack'
              ? serverSubgroups(mn, byModel[mn] ?? []).find((sg) => sg.patches.some((p) => p.name === name))?.server ?? null
              : null
          const skKey = sk === null ? null : `${mk}/${sk}`
          setMeshPreviewCollapsed((prev) =>
            prev[kind] || prev[mk] || (skKey !== null && prev[skKey])
              ? { ...prev, [kind]: false, [mk]: false, ...(skKey !== null ? { [skKey]: false } : {}) }
              : prev,
          )
          return
        }
      }
    },
  })

  useEffect(
    () =>
      registerPatchPickSource({
        // Above the post-processing surface: the preview is drawn over it, so
        // it is what a click on screen is aimed at.
        priority: 1,
        active: () => {
          const s = getMeshPreviewSession()
          return !!s.built && s.visible
        },
        // Raycaster ignores `.visible`, so an unchecked wall between the camera
        // and the target would win the hit and select a patch that is not drawn.
        //
        // In MERGED mode the drawn object is the single merged surface and the
        // per-patch meshes are not in the scene at all: raycasting 8,000 of
        // them (Alibaba-hongchou) froze the tab for seconds per click and still
        // resolved nothing, because the hit object is the merged surface, which
        // is not in `byPatch`. `faceIndex` counts triangles of the merged index
        // buffer, and `rebuildMerged` records where each patch landed in it.
        meshes: () => {
          const built = getMeshPreviewSession().built
          if (!built) return []
          if (built.merged) return built.mergedPair?.surface ? [built.mergedPair.surface] : []
          return [...built.byPatch.entries()]
            .filter(([name]) => !getMeshPreviewSession().hidden.has(name))
            .map(([, entry]) => entry.surface)
        },
        resolve: (hit) => {
          const session = getMeshPreviewSession()
          const built = session.built
          if (!built) return null
          if (built.merged) {
            if (hit.object !== built.mergedPair?.surface) return null
            return typeof hit.faceIndex === 'number'
              ? mergedPatchAtTriangle(built, hit.faceIndex)
              : null
          }
          for (const [name, entry] of built.byPatch.entries()) {
            if (entry.surface === hit.object) return name
          }
          return null
        },
        container: () => controls.containerRef.current,
        model: () => controls.selectionRef.current,
      }),
    [controls],
  )

  function toggle(targets: readonly string[], visible: boolean) {
    setMeshPreviewPatchesVisible(targets, visible)
  }

  function toggleAll(visible: boolean) {
    // 全选/全不选 is a group gesture like any other: with a filter active it
    // means the patches on screen (`mpToggleAll`, STL_generate.html:24565).
    toggle(controls.onScreen(names), visible)
  }

  function patchRow(p: PatchLike) {
    return (
      <div
        className="pp-patch-row"
        data-patch={p.name}
        key={p.name}
        onClick={(e) => controls.onRowClick(p.name, e)}
      >
        <input
          type="checkbox"
          className="mp-cb"
          data-patch={p.name}
          checked={!hidden.has(p.name)}
          onChange={(e) => {
            const { targets, visible } = controls.checkboxTargets(p.name, e)
            toggle(targets, visible)
          }}
        />
        <span title={`${p.nTriangles} tris`}>
          {p.name} <span className="dim">({p.nTriangles})</span>
        </span>
      </div>
    )
  }

  return (
    <div id="mesh-preview-right" className="content">
      <div className="prop-row">
        <button
          className="btn success"
          id="mp-load-btn"
          disabled={busy}
          onClick={() => void loadMeshPreviewSession()}
        >
          {busy ? t('mp.loading_mesh') : t('mp.load')}
        </button>
        <button className="btn danger" id="mp-close-btn" onClick={closeMeshPreviewSession}>
          {t('mp.close')}
        </button>
      </div>
      {busy && (
        <PpInitTask
          title={t('mp.loading_mesh')}
          lines={loadLines}
          warnings={[]}
          onCancel={abortMeshPreviewLoad}
        />
      )}
      {error && <div className="error">{error}</div>}
      <div id="mp-mesh-summary" className="prop-hint">
        {summary}
      </div>

      <div className="prop-row">
        <label title={t('mp.read_zones_hint')}>{t('mp.read_zones')}</label>
        {/* Disabled here as in the legacy: cellZones are not in the preview
            payload, so offering the switch would promise something the
            endpoint does not send. */}
        <input id="mp-read-zones" type="checkbox" disabled />
      </div>

      <div className="prop-group">
        <div className="prop-row">
          <label>{t('mp.mesh_opacity')}</label>
          <span id="mp-opacity-val" className="mono-pct">
            {Math.round(meshOpacity * 100)}%
          </span>
        </div>
        <input
          id="mp-opacity"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={meshOpacity}
          onChange={(e) => setMeshPreviewOpacity(Number(e.target.value))}
          style={{ width: '100%' }}
        />
        <div className="prop-row">
          <label>{t('mp.geo_opacity')}</label>
          <span id="mp-geo-opacity-val" className="mono-pct">
            {Math.round(geoOpacity * 100)}%
          </span>
        </div>
        <input
          id="mp-geo-opacity"
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={geoOpacity}
          onChange={(e) => setMeshPreviewGeoOpacity(Number(e.target.value))}
          style={{ width: '100%' }}
        />
      </div>

      {/* Which layer wins the depth fight. Two chips here, not five: the mesh
          tab draws the preview and the model geometry and nothing else. */}
      <div className="prop-group">
        <RenderOrderChips panelId="mp-render-order-panel" order={order} onReorder={setMeshPreviewOrder} />
      </div>

      <div className="prop-group">
        <div className="prop-row">
          <label>Patches</label>
          <button className="node-action-btn" id="mp-all-on" onClick={() => toggleAll(true)}>
            {t('btn.select_all')}
          </button>
          <button className="node-action-btn" id="mp-all-off" onClick={() => toggleAll(false)}>
            {t('btn.deselect_all')}
          </button>
        </div>
        <div id="mp-patch-list" ref={controls.containerRef}>
          {/* Same empty host as the post-processing tree: the filter bar is
              foreign DOM and must not sit among children React reconciles. */}
          <div className="patch-filter-host" ref={controls.filterHostRef} />

          {patches.length === 0 && <div className="prop-hint">{t('mp.no_mesh_loaded')}</div>}

          {/* Above the tree, as the legacy's #mp-enclosure-warning. No
              data-patch, so the filter and the selection glue pass over it. */}
          {missingEnclosure.length > 0 && (
            <div
              id="mp-enclosure-warning"
              className="prop-hint"
              data-missing={missingEnclosure.join(',')}
              style={{
                margin: '0 0 6px',
                padding: '6px 8px',
                border: '1px solid rgba(255,70,70,0.55)',
                background: 'rgba(255,70,70,0.08)',
                color: '#ff9b9b',
                fontSize: 11,
                lineHeight: 1.5,
              }}
            >
              {t('mp.enclosure_missing', { names: missingEnclosure.join(', ') })}
            </div>
          )}

          {kinds.map((kind) => {
            const byModel = groups[kind] ?? {}
            const modelNames = Object.keys(byModel).sort()
            const flat = isFlatKind(byModel)
            const kindNames = modelNames.flatMap((m) => (byModel[m] ?? []).map((p) => p.name))
            // Over what is ON SCREEN, because that is what the box acts on.
            const allOn = controls.onScreen(kindNames).every((n) => !hidden.has(n))
            const open = !collapsed[kind]
            return (
              <div className="pp-kind" key={kind}>
                <div className="pp-kind-head" style={{ color: KIND_COLORS[kind] ?? '#999' }}>
                  <span
                    className="pp-caret"
                    onClick={() =>
                      setMeshPreviewCollapsed((c) => ({ ...c, [kind]: !c[kind] }))
                    }
                  >
                    {open ? '▼' : '▶'}
                  </span>
                  <input
                    type="checkbox"
                    className="patch-group-cb"
                    data-kind={kind}
                    checked={allOn}
                    onChange={(e) => toggle(controls.onScreen(kindNames), e.target.checked)}
                  />
                  {KIND_LABELS[kind] ?? 'OTHER'}{' '}
                  <span className="dim">({kindPatchCount(groups, kind)})</span>
                </div>
                {open && (
                  <div className="pp-kind-body">
                    {flat
                      ? (byModel[modelNames[0] as string] ?? []).map(patchRow)
                      : modelNames.map((mn) => {
                          const list = byModel[mn] ?? []
                          const groupNames = list.map((p) => p.name)
                          const mOpen = !collapsed[`${kind}/${mn}`]
                          return (
                            <div key={mn}>
                              <div className="pp-model-head">
                                <span
                                  className="pp-caret"
                                  onClick={() =>
                                    setMeshPreviewCollapsed((c) => ({
                                      ...c,
                                      [`${kind}/${mn}`]: !c[`${kind}/${mn}`],
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
                                    .onScreen(groupNames)
                                    .every((n) => !hidden.has(n))}
                                  onChange={(e) =>
                                    toggle(controls.onScreen(groupNames), e.target.checked)
                                  }
                                />
                                {patchGroupLabel(mn, t('mp.domain_boundary'))}{' '}
                                <span className="dim">({list.length})</span>
                              </div>
                              {mOpen && (
                                <div className="pp-model-body">
                                  {kind === 'rack'
                                    ? serverSubgroups(mn, list).map((sg) =>
                                        sg.server === null ? (
                                          sg.patches.map(patchRow)
                                        ) : (
                                          <div key={sg.server} className="pp-server">
                                            <div className="pp-server-head">
                                              <span
                                                className="pp-caret"
                                                onClick={() =>
                                                  setMeshPreviewCollapsed((c) => ({
                                                    ...c,
                                                    [`${kind}/${mn}/${sg.server}`]: !c[`${kind}/${mn}/${sg.server}`],
                                                  }))
                                                }
                                              >
                                                {collapsed[`${kind}/${mn}/${sg.server}`] ? '▶' : '▼'}
                                              </span>
                                              <input
                                                type="checkbox"
                                                className="patch-server-cb"
                                                data-server={sg.server}
                                                checked={controls
                                                  .onScreen(sg.patches.map((p) => p.name))
                                                  .every((n) => !hidden.has(n))}
                                                onChange={(e) =>
                                                  toggle(controls.onScreen(sg.patches.map((p) => p.name)), e.target.checked)
                                                }
                                              />
                                              {sg.server} <span className="dim">({sg.patches.length})</span>
                                            </div>
                                            {!collapsed[`${kind}/${mn}/${sg.server}`] && (
                                              <div className="pp-server-body">{sg.patches.map(patchRow)}</div>
                                            )}
                                          </div>
                                        ),
                                      )
                                    : list.map(patchRow)}
                                </div>
                              )}
                            </div>
                          )
                        })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
