import { Fragment, useEffect, useRef, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import type { TranslationKey } from '../i18n'
import {
  meshConfigOf,
  REFINEMENT_ROWS,
  simulationConfigOf,
  MESH_ENGINES,
  resolveMeshEngine,
  type MeshEngine,
} from '../run/meshConfig'
import * as store from '../projectStore'
import * as session from '../run/session'
import { readCasePath } from '../run/caseDir'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { meshSizing, type SizingRow } from '../legacy/meshSizing'
import { getDragAxes, getMeshVisuals, setDragAxis, subscribeMeshView } from '../run/meshViewState'
import { formatBounds, MAX_GRID_LINES } from '../run/meshVisuals'
import { blockMeshBounds } from '../legacy/cfdrunBuild'
import { autoDetectRefinement } from '../geometry/autoRefine'
import { allModels } from '../schema/cfdproj'
import { getViewportHandle } from '../viewportBus'
import {
  displayBound,
  renameRefinementZone,
  setRefinementZoneBound,
  setRefinementZoneVisible,
  type Axis,
  type Bound,
  type ZoneBox,
} from '../run/refinementZones'
import * as THREE from 'three'
import type { ExtraLocation } from '../projectStore'
import { FreezeGuard } from './FreezeGuard'
import { LIVE_CLASS, RUN_BLOCK_CLASS } from '../run/uiFreeze'
import { coreWarning } from '../run/coreCount'
import { MeshCheckDetail } from './MeshCheckDetail'
import { REFINEMENT_COLUMNS, REFINEMENT_TOOLTIPS } from '../run/refinementRowMeta'
import {
  closeMeshProgress,
  openMeshProgress,
  setMeshProgressStep,
  setMeshProgressTitle,
} from '../run/meshPreflightUi'

/**
 * The mesh tab.
 *
 * Sections and their colours mirror the legacy panel, because this tab is the
 * one a user compares against the HTML app most often — the numbers here decide
 * whether a mesh takes 40 seconds or 15 minutes.
 *
 * Two things the legacy panel gets right that are easy to lose in a port:
 *
 * - **Feature levels are SCALARS.** They sit in the same row as the `[min, max]`
 *   surface pair, but `setMeshRefineLevel` special-cases every key ending in
 *   `FeatureLevel` (and `rackRegionLevel`) and ignores the index. Writing a
 *   2-element array there produces a snappyHexMeshDict the runner cannot read.
 * - **Partition-wall levels live in `simulationConfig`,** not `meshConfig`,
 *   despite sitting in this section. Writing them to `meshConfig` means the
 *   runner silently uses the default.
 */

// The legacy mesh-mode section is hardcoded Chinese with no data-i18n; these
// keys were added so this panel is not half-translated under an English locale.
const MESH_ENGINE_LABEL_KEY: Record<MeshEngine, TranslationKey> = {
  snappy: 'meshengine.snappy',
  dcCart: 'meshengine.dcCart',
}

const MESH_CELL_LIMIT_HELP_KEY: Record<MeshEngine, TranslationKey> = {
  snappy: 'hint.max_cells_desc',
  dcCart: 'hint.max_cells_dcCart',
}

const MESH_ENGINE_SUMMARY_KEY: Record<MeshEngine, TranslationKey> = {
  snappy: 'meshengine.summary.snappy',
  dcCart: 'meshengine.summary.dcCart',
}

const MESH_ENGINE_DESC_KEY: Record<MeshEngine, TranslationKey> = {
  snappy: 'meshengine.desc.snappy',
  dcCart: 'meshengine.desc.dcCart',
}

export interface MeshPanelProps {
  project: CfdProject | null
}

export function MeshPanel({ project }: MeshPanelProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeMeshView(() => bump((n) => n + 1)), [])

  const mesh = meshConfigOf(project)
  const sim = simulationConfigOf(project)
  const engine = resolveMeshEngine(mesh)
  const dragAxes = getDragAxes()
  const [autoDetect, setAutoDetect] = useState<string | null>(null)
  const extraSeeds = (sim.extraLocations ?? []) as ExtraLocation[]

  /**
   * Point the camera at the seed.
   *
   * Moves the orbit TARGET and keeps the current viewing direction and
   * distance, so the room does not spin when the button is pressed — the
   * legacy `locateLocInMesh` does the same (STL_generate.html:27304).
   */
  function locateSeed() {
    const handle = getViewportHandle()
    const loc = mesh.locationInMesh as { x: number; y: number; z: number } | undefined
    if (!handle || !loc) return
    const { camera, controls } = handle
    const dir = new THREE.Vector3()
    camera.getWorldDirection(dir)
    const dist = camera.position.distanceTo(controls.target)
    controls.target.set(loc.x, loc.y, loc.z)
    camera.position.set(loc.x - dir.x * dist, loc.y - dir.y * dist, loc.z - dir.z * dist)
    controls.update()
  }

  /**
   * Fly to an extra seed.
   *
   * A different gesture from `locateSeed` above, and deliberately so: the
   * legacy's `_flyToExtraLoc` (STL_generate.html:27442) puts the camera 3 m off
   * the seed diagonally instead of keeping the current distance. On a 40 m room
   * a re-aim leaves the seed a dot in the middle of the screen — fine for the
   * one seed that has its own sphere and its own drag handles, useless for
   * picking one of six out of a list.
   */
  function flyToExtraSeed(entry: ExtraLocation) {
    const handle = getViewportHandle()
    if (!handle) return
    const { camera, controls } = handle
    controls.target.set(entry.x, entry.y, entry.z)
    camera.position.set(entry.x + 3, entry.y + 3, entry.z + 3)
    controls.update()
  }

  const info = session.getServerInfo()

  function set(key: string, value: unknown) {
    store.setRunConfig('meshConfig', key, value)
  }

  // Half the LOGICAL core count, which is the physical one: see `coreCount.ts`.
  // The old test here was `procs > cpuCount`, twice this threshold, so on a
  // 2-way-SMT host it could not fire until every hyperthread was oversubscribed.
  const parallelMesh = mesh.parallelMesh !== false
  const cores = coreWarning(mesh.meshProcess, info?.cpuCount)

  return (
    <div className="content" id="mesh-settings-panel">
      {/* Everything down to the run block freezes while a job runs; the run
          block itself does not, so the abort button stays usable. */}
      <FreezeGuard>
      {/* ── global ─────────────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-create)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-create)' }}>
          {t('title.mesh_global')}
        </div>
        <div className="prop-row">
          <label htmlFor="ms-base-size">{t('label.base_size')}</label>
          <NumberField id="ms-base-size" value={mesh.meshBaseSize} step={0.01} min={0.01} max={2} onCommit={(v) => set('meshBaseSize', v)} />
        </div>
        <div className="prop-row">
          <label htmlFor="ms-max-cells">{t('label.max_cells')}</label>
          <NumberField id="ms-max-cells"
            value={mesh.maxGlobalCells}
            step={100000}
            min={10000}
            onCommit={(v) => set('maxGlobalCells', Math.round(v))}
          />
        </div>
        <div className="prop-hint" id="ms-cell-limit-help">{t(MESH_CELL_LIMIT_HELP_KEY[engine])}</div>
        <div className="prop-row">
          <label htmlFor="ms-parallel">{t('label.parallel_mesh')}</label>
          <input id="ms-parallel"
            type="checkbox"
            checked={parallelMesh}
            onChange={(e) => set('parallelMesh', e.target.checked)}
          />
        </div>
        {/* Switched off with parallel, as the legacy does (:25770). A live box
            over a serial run is a number that changes nothing. */}
        <div className="prop-row" style={{ opacity: parallelMesh ? 1 : 0.4 }}>
          <label htmlFor="ms-procs">{t('label.procs')}</label>
          <NumberField id="ms-procs"
            value={mesh.meshProcess}
            step={1}
            min={1}
            max={128}
            disabled={!parallelMesh}
            onCommit={(v) => set('meshProcess', Math.round(v))}
          />
        </div>
        {cores && (
          <div id="ms-procs-warning" className="prop-hint" style={{ color: 'var(--hint-color)' }}>
            {t('status.cores_warning', { ...cores })}
          </div>
        )}
      </div>

      {/* ── mesh engine ────────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-refine)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-refine)' }}>
          {t('title.mesh_engine')}
        </div>
        <div className="prop-row">
          <label htmlFor="ms-mesh-engine">{t('label.mesh_engine')}</label>
          <select
            id="ms-mesh-engine"
            value={engine}
            onChange={(e) => set('meshEngine', e.target.value)}
            style={{ flex: 1 }}
          >
            {MESH_ENGINES.map((m) => (
              <option key={m} value={m}>
                {t(MESH_ENGINE_LABEL_KEY[m])}
              </option>
            ))}
          </select>
        </div>
        <div className="prop-hint" id="ms-mesh-engine-summary">{t(MESH_ENGINE_SUMMARY_KEY[engine])}</div>
        <details className="mesh-engine-details">
          <summary>{t('meshengine.advanced_details')}</summary>
          <div className="prop-hint" id="ms-mesh-engine-desc">{t(MESH_ENGINE_DESC_KEY[engine])}</div>
        </details>
        <div className="prop-row">
          <label htmlFor="ms-first-cell">{t('label.first_cell_size')}</label>
          <NumberField
            id="ms-first-cell"
            value={mesh.firstCellSize == null ? null : Number(mesh.firstCellSize)}
            step={0.005}
            min={0}
            placeholder="auto"
            onCommit={(v) => set('firstCellSize', v > 0 ? v : null)}
            onClear={() => set('firstCellSize', null)}
          />
        </div>
      </div>

      {/* ── dcCart sizing: the dcCart floors + the readout ─────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-refine)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-refine)' }}>
          {t('title.sixsigma_sizing')}
        </div>
        <div className="prop-hint">{t('hint.sixsigma_desc')}</div>
        {SIZING_FIELDS.map(([key, labelKey, tipKey, step, placeholder]) => (
          <div className="prop-row" key={key}>
            <label htmlFor={`ms-${key}`} title={t(tipKey)}>{t(labelKey)}</label>
            <NumberField
              id={`ms-${key}`}
              value={mesh[key] == null ? null : Number(mesh[key])}
              step={step}
              placeholder={placeholder}
              onCommit={(v) => set(key, v)}
              onClear={() => set(key, null)}
            />
          </div>
        ))}
        <button
          className="btn"
          style={{ width: '100%', marginTop: 6 }}
          title={t('tooltip.apply_sixsigma')}
          onClick={() => {
            const res = meshSizing().dcCartProductMeshConfig(project)
            // One checkpoint for the whole preset, like the legacy button.
            store.edit('applyDcCartMeshPreset', `${res.notes.length} keys`, (draft) => {
              const rd = (draft as unknown as {
                runDefaults: { meshConfig: Record<string, unknown>; simulationConfig: Record<string, unknown> }
              }).runDefaults
              for (const [k, v] of Object.entries(res.patch)) {
                // partition-wall levels live in simulationConfig (see meshConfig.ts).
                ;(k.startsWith('partitionWall') ? rd.simulationConfig : rd.meshConfig)[k] = v
              }
            })
          }}
        >
          {t('btn.apply_sixsigma')}
        </button>
        <SizingReadout project={project} />
      </div>

      {/* ── refinement ─────────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-refine)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-refine)' }}>
          {t('title.refinement')}
        </div>
        <div className="prop-hint">{t('hint.refinement_desc')}</div>
        {/* A real 4-column grid, as the legacy is (STL_generate.html:189), not a
            stack of prop-rows: the three boxes in a row are three DIFFERENT
            settings, and without the headings above the columns there is
            nothing on screen saying which is surface-min, surface-max and
            feature-line. The grid is also what lets the rack row's single box
            span the three value columns instead of sitting under "surf min". */}
        <div
          id="ms-refine-grid"
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr 1fr auto',
            gap: '2px 4px',
            alignItems: 'center',
            fontSize: '0.82rem',
            padding: '0 8px 4px',
          }}
        >
          <span />
          {REFINEMENT_COLUMNS.map((key) => (
            <span
              key={key}
              style={{ textAlign: 'center', fontSize: '0.72rem', color: 'var(--text-dim)' }}
            >
              {t(key)}
            </span>
          ))}
          {REFINEMENT_ROWS.map((row) => {
            const source: Record<string, unknown> = row.bucket === 'meshConfig' ? mesh : sim
            const pair = (source[row.surfaceKey ?? ''] as [number, number] | undefined) ?? [1, 1]
            const feature = Number(source[row.featureKey ?? ''] ?? 2)
            const tip = REFINEMENT_TOOLTIPS[row.id]
            return (
              <Fragment key={row.id}>
                <label title={tip ? t(tip) : undefined}>{t(row.labelKey as 'label.acu')}</label>
                {row.surfaceKey ? (
                  <>
                    <NumberField
                      id={row.surfaceDomId ? `${row.surfaceDomId}-0` : undefined}
                      ariaLabel={`${t(row.labelKey as 'label.acu')} ${t(REFINEMENT_COLUMNS[0]!)}`}
                      className="mesh-lvl-input"
                      value={Number(pair[0])}
                      step={1}
                      min={0}
                      max={8}
                      onCommit={(v) =>
                        store.setRefinementBound(row.bucket, row.surfaceKey!, 0, Math.round(v), pair)
                      }
                    />
                    <NumberField
                      id={row.surfaceDomId ? `${row.surfaceDomId}-1` : undefined}
                      ariaLabel={`${t(row.labelKey as 'label.acu')} ${t(REFINEMENT_COLUMNS[1]!)}`}
                      className="mesh-lvl-input"
                      value={Number(pair[1])}
                      step={1}
                      min={0}
                      max={8}
                      onCommit={(v) =>
                        store.setRefinementBound(row.bucket, row.surfaceKey!, 1, Math.round(v), pair)
                      }
                    />
                  </>
                ) : null}
                {row.featureKey && (
                  // A row with no surface pair owns the whole value area — the
                  // rack region level is neither a surface nor a feature level,
                  // so parking it under one of those headings would name it
                  // wrongly. The legacy spans it the same way (:197).
                  <span style={row.surfaceKey ? { display: 'contents' } : { gridColumn: 'span 3', maxWidth: 60 }}>
                    <NumberField
                      id={row.featureDomId}
                      ariaLabel={row.surfaceKey ? `${t(row.labelKey as 'label.acu')} ${t(REFINEMENT_COLUMNS[2]!)}` : t(row.labelKey as 'label.acu')}
                      className="mesh-lvl-input"
                      value={feature}
                      step={1}
                      min={0}
                      max={8}
                      // Scalar on purpose — see the header note.
                      onCommit={(v) => store.setRunConfig(row.bucket, row.featureKey!, Math.round(v))}
                    />
                  </span>
                )}
              </Fragment>
            )
          })}
        </div>
        {/* Auto-detect: a 40 mm partition wall under a 0.34 m base cell never
            gets a surface at all, and the level that fixes it is a computation,
            not a guess. */}
        <button
          className="btn"
          id="btn-auto-detect-refinement"
          onClick={() => {
            const models = project ? allModels(project).map((m) => m.model) : []
            const result = autoDetectRefinement(project, models)
            const message = result
              ? result.changed
                ? t('log.auto_adjust_refinement', {
                    changes: `partitionWall D=${result.thickness.toFixed(3)}m [${result.oldLevel.join(',')}]→[${result.newLevel.join(',')}]`,
                  })
                : t('log.auto_adjust_refinement_no_changes')
              : t('log.auto_adjust_refinement_no_changes')
            setAutoDetect(message)
            store.logOperation('mesh', `${t('btn.auto_detect_refinement')}: ${message}`)
          }}
        >
          {t('btn.auto_detect_refinement')}
        </button>
        {autoDetect && <div className="prop-hint">{autoDetect}</div>}
        <div className="prop-row">
          <label htmlFor="ms-detect-base">{t('label.detect_base_size')}</label>
          <NumberField
            id="ms-detect-base"
            value={Number(mesh.meshDetectBaseSize ?? mesh.meshBaseSize)}
            step={0.01}
            min={0}
            onCommit={(v) => set('meshDetectBaseSize', v > 0 ? v : null)}
            onClear={() => set('meshDetectBaseSize', null)}
          />
        </div>
      </div>

      {/* ── blockMesh domain ───────────────────────────────────────────── */}
      <BlockMeshGroup project={project} />

      {/* ── locationInMesh ─────────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-location)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-location)' }}>
          {t('label.location_in_mesh')}
        </div>
        <div className="prop-hint">{t('hint.location_in_mesh')}</div>
        {(['x', 'y', 'z'] as const).map((axis) => (
          <div className="prop-row" key={axis}>
            <label htmlFor={`slv-loc-${axis}`}>{axis.toUpperCase()}</label>
            <NumberField
              // `slv-loc-*`, NOT `loc-axis-*`: the latter is the drag-axis constraint
              // checkbox next to this row (STL_generate.html:248), a different
              // control entirely. The id map was keyed by the label's i18n key and
              // this row's labels are bare "X"/"Y"/"Z", so it matched the wrong one.
              id={`slv-loc-${axis}`}
              ariaLabel={`${t('label.location_in_mesh')} ${axis.toUpperCase()}`}
              value={Number(mesh.locationInMesh?.[axis] ?? 0)}
              step={0.1}
              onCommit={(v) => store.setLocationInMesh(axis, v)}
            />
          </div>
        ))}
        <div className="prop-row">
          <label htmlFor="loc-visible-cb">{t('common.display')}</label>
          <input
            id="loc-visible-cb"
            type="checkbox"
            checked={mesh.locInMeshVisible !== false}
            onChange={(e) => set('locInMeshVisible', e.target.checked)}
          />
        </div>
        <div className="prop-row">
          <label>{t('label.drag_axis')}</label>
          <div style={{ display: 'flex', gap: 8, flex: 1, justifyContent: 'flex-end' }}>
            {(['x', 'y', 'z'] as const).map((axis) => (
              <label key={axis} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                <input
                  id={`loc-axis-${axis}`}
                  type="checkbox"
                  checked={dragAxes[axis]}
                  onChange={(e) => setDragAxis(axis, e.target.checked)}
                  style={{ width: 'auto', margin: 0 }}
                />
                {axis.toUpperCase()}
              </label>
            ))}
          </div>
        </div>
        <button className="btn" id="btn-locate-loc" onClick={locateSeed}>
          {t('btn.locate_view')}
        </button>
        <div className="prop-hint" style={{ marginTop: 6 }}>{t('hint.extra_seeds_desc')}</div>
        <div id="extra-loc-list">
          {extraSeeds.map((entry) => (
            <div className="prop-row" key={entry.id}>
              {/* The label IS the control, as it is in the legacy row: the
                  coordinates next to it are editable boxes, so the name and its
                  colour dot are what is left to click. */}
              <label
                id={`extra-loc-${entry.id}-locate`}
                title={t('tooltip.locate_seed')}
                onClick={() => flyToExtraSeed(entry)}
                style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: '50%',
                    background: entry.color,
                    display: 'inline-block',
                  }}
                />
                #{entry.id}
              </label>
              {(['x', 'y', 'z'] as const).map((axis) => (
                <NumberField
                  key={axis}
                  id={`extra-loc-${entry.id}-${axis}`}
                  value={Number(entry[axis])}
                  step={0.1}
                  onCommit={(v) => store.setExtraLocation(entry.id, axis, v)}
                />
              ))}
              <button
                className="node-action-btn"
                title={t('tooltip.delete')}
                onClick={() => store.removeExtraLocation(entry.id)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
        <button className="btn" id="btn-add-seed" onClick={() => store.addExtraLocation()}>
          {t('btn.add_seed')}
        </button>
      </div>

      {/* ── refinement zones ───────────────────────────────────────────── */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-zone)' }}>
        <div className="prop-title" style={{ color: 'var(--section-header-zone)' }}>
          {t('title.refinement_zones')}
        </div>
        <div className="prop-hint">
          {(mesh.refinementZones ?? []).length === 0
            ? t('label.no_refinement_zones')
            : t('label.zone_count', { count: mesh.refinementZones.length })}
        </div>
        <div id="rz-list">
        {(mesh.refinementZones ?? []).map((zone, i) => (
          <ZoneRow key={(zone as { id?: string }).id ?? i} zone={zone as ZoneBox} index={i} />
        ))}
        </div>
        <button className="btn" id="btn-add-zone" onClick={() => store.addRefinementZone()}>
          {t('btn.add_zone')}
        </button>
      </div>
      </FreezeGuard>

      <MeshRunBlock />
    </div>
  )
}

/**
 * The blockMesh domain: what the background grid will be.
 *
 * Read-only on purpose. The bounds are DERIVED — the room polygon padded by one
 * whole cell — and the legacy panel dropped its editable min/max boxes for the
 * same reason: a hand-typed bound that disagreed with the room silently changed
 * the cell count of every mesh.
 */
function BlockMeshGroup({ project }: { project: CfdProject | null }) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => subscribeMeshView(() => bump((n) => n + 1)), [])

  const globalGroup =
    (window as unknown as { __sceneBuilder?: { registries?: { renderRoots?: THREE.Object3D[] } } })
      .__sceneBuilder?.registries?.renderRoots?.[0]?.parent ?? null
  const bounds = project ? blockMeshBounds(project, globalGroup) : null
  const visuals = getMeshVisuals()
  const shown = visuals?.gridShown() ?? false

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-block)' }}>
      <div className="prop-title" style={{ color: 'var(--section-header-block)' }}>
        {t('title.blockmesh')}
      </div>
      <div id="bm-readonly-display" className="prop-hint">
        <div>{t('hint.blockmesh_auto_desc')}</div>
        <div id="bm-bounds-info" style={{ marginTop: 4, fontFamily: 'monospace', fontSize: '0.72rem' }}>
          {bounds ? formatBounds(bounds) : '--'}
        </div>
      </div>
      <button
        className={`btn${shown ? ' primary' : ''}`}
        id="bm-grid-preview-btn"
        onClick={() => {
          const r = visuals?.toggleGrid()
          const refused = r?.refused
            ? t('error.grid_too_many_lines', { count: r.refused })
            : null
          setNote(refused)
          store.logOperation(
            'mesh',
            `${shown ? t('btn.hide_grid_preview') : t('btn.preview_grid')}${refused ? `: ${refused}` : ''}`,
          )
          bump((n) => n + 1)
        }}
      >
        {shown ? t('btn.hide_grid_preview') : t('btn.preview_grid')}
      </button>
      {note && <div className="error">{note}</div>}
      {/* The cap is the difference between a preview and a frozen tab; saying
          the number is what lets someone raise the base size on purpose. */}
      {!note && shown && (
        <div className="prop-hint">{t('hint.grid_line_cap', { count: MAX_GRID_LINES })}</div>
      )}
    </div>
  )
}

/**
 * One refinement zone in the list.
 *
 * The legacy row (`_rebuildRefinementZoneList`, STL_generate.html:26997) carries
 * four things this port had dropped: a visibility checkbox, an editable name,
 * and the six MIN/MAX boxes. Without them a zone can only be placed by dragging
 * it in the viewport — so an exact 1.20 m box is unreachable, and three zones
 * called RefineZone_1..3 cannot be told apart in the list.
 */
function ZoneRow({ zone, index }: { zone: ZoneBox; index: number }) {
  const { t } = useTranslation()
  const id = zone.id ?? ''
  const [name, setName] = useState(zone.name ?? `RefineZone_${index + 1}`)
  const [editing, setEditing] = useState(false)
  // Re-sync from the project when it changes underneath (an undo, a reload),
  // but never while the box has focus, or the edit in progress is overwritten.
  useEffect(() => {
    if (!editing) setName(zone.name ?? `RefineZone_${index + 1}`)
  }, [zone.name, index, editing])

  return (
    <div className="rz-item" data-zone-id={id}>
      <div className="prop-row">
        <input
          type="checkbox"
          id={`rz-visible-${index}`}
          checked={zone.visible !== false}
          onChange={(e) => setRefinementZoneVisible(id, e.target.checked)}
          style={{ width: 'auto', margin: 0, accentColor: 'var(--section-header-zone)' }}
        />
        <input
          type="text"
          id={`rz-name-${index}`}
          value={name}
          onFocus={() => setEditing(true)}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            setEditing(false)
            if (name.trim() && name !== zone.name) renameRefinementZone(id, name)
          }}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          style={{ flex: 1, minWidth: 0 }}
        />
        <label style={{ flex: '0 0 auto' }}>Lv</label>
        <NumberField
          id={`rz-level-${index}`}
          value={Number(zone.level ?? 2)}
          step={1}
          min={1}
          max={6}
          width={44}
          onCommit={(v) => store.setRefinementZoneField(id, 'level', Math.round(v))}
        />
        <button
          className="node-action-btn"
          title={t('tooltip.delete')}
          onClick={() => id && store.removeRefinementZone(id)}
        >
          ✕
        </button>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr 1fr',
          gap: '2px 4px',
          margin: '2px 0 6px 8px',
          alignItems: 'center',
          fontSize: '0.68rem',
          color: 'var(--section-header-zone)',
        }}
      >
        <span />
        <span style={{ textAlign: 'center' }}>MIN</span>
        <span style={{ textAlign: 'center' }}>MAX</span>
        {(['x', 'y', 'z'] as Axis[]).map((axis) => (
          <ZoneAxis key={axis} id={id} index={index} axis={axis} zone={zone} />
        ))}
      </div>
    </div>
  )
}

function ZoneAxis({
  id,
  index,
  axis,
  zone,
}: {
  id: string
  index: number
  axis: Axis
  zone: ZoneBox
}) {
  return (
    <>
      <span>{axis.toUpperCase()}</span>
      {(['min', 'max'] as Bound[]).map((bound) => (
        <NumberField
          key={bound}
          id={`rz-${bound}-${axis}-${index}`}
          value={displayBound(Number(zone[bound]?.[axis] ?? 0))}
          step={0.1}
          width={64}
          onCommit={(v) => setRefinementZoneBound(id, bound, axis, v)}
        />
      ))}
    </>
  )
}

/**
 * Run / abort, with the live server log.
 *
 * The run button submits the geometry ON SCREEN: `session.runJob` rebuilds the
 * .cfdrun manifest and the grouped STL set and posts both, so nothing has to be
 * hand-exported first.
 *
 * The session owns preparation admission and cancellation. Local `preparing`
 * keeps this run's overlay visible through the upload and first-log gap; its
 * sequence prevents a cancelled build from closing a newer run's overlay.
 */
/**
 * Hold the overlay on its last step until the server says something.
 *
 * `runJob` resolves as soon as the POST returns, but the server still has to
 * start the job — several seconds on a large case. The legacy keeps its overlay
 * up until the first log line for exactly that gap (STL_generate.html:28219).
 * Capped, so a server that answers with a job id and then goes quiet leaves a
 * usable screen rather than a modal nobody can dismiss.
 */
function waitForFirstLogLine(jobId: string, timeoutMs = 30000): Promise<void> {
  return new Promise((resolve) => {
    const job = session.getJob()
    if (!job || job.id !== jobId || !job.running || job.lines.length > 0) return resolve()
    let done = false
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(timer)
      unsubscribe()
      resolve()
    }
    const timer = setTimeout(finish, timeoutMs)
    const unsubscribe = session.subscribeSession(() => {
      const job = session.getJob()
      if (!job || job.id !== jobId || !job.running || job.lines.length > 0) finish()
    })
  })
}

function MeshRunBlock() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [checking, setChecking] = useState(false)
  const progressSequence = useRef(0)
  useEffect(() => session.subscribeSession(() => bump((n) => n + 1)), [])

  const job = session.getCurrentCaseJob()
  const preparation = session.getRunPreparation()
  const meshPreparation = preparation?.kind === 'mesh' ? preparation : null
  const running = !!session.getJob()?.running
  const validated = session.isValidated()
  const info = session.getServerInfo()
  const caseDir = readCasePath() || info?.caseDir

  function stopMesh(preparationToken?: number) {
    const current = session.getRunPreparation()
    if (preparationToken !== undefined && current?.token !== preparationToken) return
    store.logOperation('mesh', t('btn.abort_mesh'))
    if (current?.kind === 'mesh') {
      void session.abortJob(current.token)
      if (current.phase === 'preparing') {
        // A late catch/finally from this cancelled build must not close a
        // newer run's overlay or clear its local progress state.
        ++progressSequence.current
        closeMeshProgress()
        setPreparing(false)
      } else {
        const button = document.getElementById('mesh-preparation-abort-btn') as HTMLButtonElement | null
        if (button) {
          button.disabled = true
          button.textContent = t('status.stopping')
        }
      }
      return
    }
    void session.abortJob()
  }

  /**
   * Start a mesh run behind the step overlay.
   *
   * The overlay is opened HERE and closed here, not inside `buildRunRequest`:
   * the slow half is the manifest and the STL tessellation, but the job is not
   * actually submitted until `runJob` has POSTed, and closing at the end of the
   * build would blank the screen for the upload. The gate inside only ticks the
   * rows — with no overlay up it does nothing, so a solve started from another
   * panel is unaffected.
   */
  async function run() {
    if (preparing || checking || session.getJob()?.running || session.getRunPreparation()) return
    const sequence = ++progressSequence.current
    setError(null)
    setPreparing(true)
    openMeshProgress(t('progress.preparing_mesh'))
    // The dock records the gesture as well as the stream: a run that never
    // reached the server must still leave a line saying it was asked.
    store.logOperation('mesh', `${t('btn.run_mesh')} · ${caseDir ?? ''}`)
    try {
      const pending = session.runJob('mesh')
      const token = session.getRunPreparation()?.token
      const overlayPanel = document.querySelector('#mesh-progress-overlay > div')
      if (token !== undefined && overlayPanel) {
        // The progress overlay covers the sidebar; keep its own stop action
        // bound to this preparation, using the same session cancellation.
        const button = document.createElement('button')
        button.id = 'mesh-preparation-abort-btn'
        button.className = 'btn danger ' + LIVE_CLASS
        button.style.margin = '0 18px 14px'
        button.textContent = t('btn.abort_mesh')
        button.onclick = () => stopMesh(token)
        overlayPanel.appendChild(button)
      }
      await pending
      if (progressSequence.current !== sequence) return
      document.getElementById('mesh-preparation-abort-btn')?.remove()
      const currentJob = session.getCurrentCaseJob()
      if (!currentJob || currentJob.kind !== 'mesh' || !currentJob.running ||
          currentJob.abortPending || currentJob.abortRequested) return
      setMeshProgressStep('init')
      setMeshProgressTitle(t('progress.server_processing'))
      await waitForFirstLogLine(currentJob.id)
    } catch (err) {
      if (progressSequence.current !== sequence || (err instanceof Error && err.name === 'AbortError')) return
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      store.logOperation('mesh', message)
    } finally {
      if (progressSequence.current === sequence) {
        closeMeshProgress()
        setPreparing(false)
      }
    }
  }

  async function check() {
    if (preparing || checking || running || session.getRunPreparation()) return
    setError(null)
    setChecking(true)
    store.logOperation('checkMesh', t('btn.check_mesh'))
    try {
      await session.runCheckMesh()
    } catch (err) {
      const message = err instanceof Error && err.message === 'mesh not found'
        ? t('status.mesh_not_found')
        : t('status.check_failed', { error: err instanceof Error ? err.message : String(err) })
      setError(message)
      store.logOperation('checkMesh', message)
    } finally {
      setChecking(false)
    }
  }

  const qualityJob = job?.kind === 'mesh' || job?.kind === 'checkMesh' ? job : null
  const qualityOk = qualityJob?.returncode === 0 && !qualityJob.aborted && !qualityJob.budgetExceeded &&
    qualityJob.checkMeshOk === true && (qualityJob.failedChecks === null || qualityJob.failedChecks === 0)

  return (
    <div className={RUN_BLOCK_CLASS} style={{ margin: 8 }}>
      {caseDir ? <details className="case-path-display">
        <summary className="prop-hint" title={caseDir} aria-label={`${t('label.case_dir')}: ${caseDir}`}>
          <span>{t('label.case_dir')}: {caseDir}</span>
        </summary>
        <div className="case-path-full">{caseDir}</div>
      </details> : <div className="prop-hint" style={{ marginBottom: 6 }}>{t('hint.no_case_dir')}</div>}
      <button
        className="btn success"
        id="mesh-run-btn"
        disabled={!validated || running || !!preparation || preparing || checking}
        title={validated ? undefined : t('error.validate_dctwin_first')}
        onClick={() => void run()}
      >
        {preparing ? t('progress.preparing_mesh') : job?.kind === 'mesh' && job.running ? t('status.meshing') : t('btn.run_mesh')}
      </button>
      {(meshPreparation || job?.kind === 'mesh' && job.running) && (
        <button
          className={`btn danger ${LIVE_CLASS}`}
          id="mesh-abort-btn"
          disabled={!!(meshPreparation?.abortRequested || job?.running && (job.abortPending || job.abortRequested))}
          onClick={() => stopMesh(meshPreparation?.token)}
        >
          {meshPreparation?.abortRequested || job?.running && (job.abortPending || job.abortRequested)
            ? t('status.stopping') : t('btn.abort_mesh')}
        </button>
      )}
      <button
        className={`btn ${LIVE_CLASS}`}
        id="mesh-check-btn"
        disabled={!validated || running || !!preparation || preparing || checking}
        title={validated ? undefined : t('error.validate_dctwin_first')}
        onClick={() => void check()}
      >
        {checking || job?.kind === 'checkMesh' && job.running
          ? t('status.running_checkmesh') : t('btn.check_mesh')}
      </button>
      {session.getCaseStatus()?.meshExists && !session.isMeshReady() && !running && (
        <div className="prop-hint" role="status">{t('hint.mesh_quality_advisory')}</div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
      {qualityJob?.error && <div className={`error ${LIVE_CLASS}`} role="alert">{qualityJob.error}</div>}
      {/* The same mesh preview the right column's sub-tab shows. The legacy
          puts this shortcut at the bottom of the run block, where a user lands
          the moment a mesh finishes (STL_generate.html:268). */}
      <button
        className="btn"
        id="mp-show-mesh"
        onClick={() => {
          store.logOperation('meshPreview', t('mp.show_mesh'))
          ;(window as unknown as { __showMeshPreview?: () => void }).__showMeshPreview?.()
        }}
      >
        {t('mp.show_mesh')}
      </button>
      <div id="mesh-run-status">
      {qualityJob && !qualityJob.running && (qualityJob.returncode !== null || qualityJob.aborted) && (
        <div className="prop-hint" role="status" style={{ color: qualityJob.aborted ? 'var(--hint-color)' : qualityJob.returncode !== 0 ? '#ff5c72' : !qualityOk ? '#ffb800' : 'var(--highlight)' }}>
          {qualityJob.aborted
            ? t('status.aborted')
            : qualityJob.returncode !== 0
              ? t('status.mesh_failed', { code: qualityJob.returncode ?? '?' })
              : !qualityOk
                ? t('status.checkmesh_rejected')
                : qualityJob.kind === 'checkMesh' ? t('status.checkmesh_ok') : t('status.mesh_ok')}
        </div>
      )}
      </div>
      <MeshCheckDetail />
    </div>
  )
}

// The five dcCart sizing keys, in panel order: key, label, tooltip, step, placeholder
// (the placeholder is the runner default that an empty field resolves to).
const SIZING_FIELDS: Array<[keyof MeshConfigSizingKeys, TranslationKey, TranslationKey, number, string]> = [
  ['weldTolerance', 'label.weld_tolerance', 'tooltip.weld_tolerance', 0.0001, '0.0012'],
  ['firstLayerMinFraction', 'label.first_layer_fraction', 'tooltip.first_layer_fraction', 0.05, '0.3'],
  ['firstLayerMax', 'label.first_layer_max', 'tooltip.first_layer_max', 0.001, '0.005'],
  ['firstLayerSealGap', 'label.first_layer_gap', 'tooltip.first_layer_gap', 0.001, '-1'],
  ['rackCellMinSize', 'label.rack_cell_min', 'tooltip.rack_cell_min', 0.0025, 'off'],
]

interface MeshConfigSizingKeys {
  weldTolerance?: number | null
  firstLayerMinFraction?: number | null
  firstLayerMax?: number | null
  firstLayerSealGap?: number | null
  rackCellMinSize?: number | null
}

/** What the mesher will build, in mm per surface class, from shared/js/mesh_sizing.js. */
function SizingReadout({ project }: { project: CfdProject | null }) {
  const { t } = useTranslation()
  let d: ReturnType<ReturnType<typeof meshSizing>['describeMeshSizing']>
  try {
    d = meshSizing().describeMeshSizing(project)
  } catch {
    return null
  }
  const names: Record<SizingRow['key'], TranslationKey> = {
    background: 'label.sizing_background', room_wall: 'label.room_wall', rack_region: 'label.rack_region',
    acu: 'label.acu', white_model: 'label.white_model', baffle_floor: 'label.baffle_floor',
  }
  const floors = Object.values(d.firstLayer.floorMmByClass)
  const over = d.budget.maxGlobalCells > 0 && d.budget.maxGlobalCells < d.budget.recommendedCells
  return (
    <div id="ms-sizing-readout" className="prop-hint" style={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
      {d.rows.map((r) => (
        <div key={r.key} style={{ color: r.cellMm < (d.sixSigma.rackCellMin ?? 0.0175) * 1000 ? '#ffaa00' : undefined }}>
          {t(names[r.key])}: L{r.level} → {r.cellMm.toFixed(1)} mm
          {r.key === 'rack_region' && d.rack.raisedFor
            ? ' ' + t('hint.sizing_raised', { mm: (d.rack.raisedFor * 1000).toFixed(1) })
            : ''}
          {r.key === 'rack_region' && d.rack.capped ? ' ' + t('hint.sizing_capped') : ''}
        </div>
      ))}
      <div>{t('hint.sizing_first_layer', { frac: d.firstLayer.fraction, mm: Math.min(...floors).toFixed(1) })}</div>
      <div style={{ color: over ? '#ff5555' : undefined }}>
        {t('hint.sizing_budget', {
          area: d.budget.areaM2.toFixed(1), racks: d.budget.racks,
          rec: d.budget.recommendedCells.toLocaleString(), cur: d.budget.maxGlobalCells.toLocaleString(),
        })}
      </div>
    </div>
  )
}
