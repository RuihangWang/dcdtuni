import { useEffect, useState } from 'react'
import { Icon } from './Icon'
import type * as THREE from 'three'
import { canBeTarget, executeBoolDiff, type BoolDiffHost } from '../geometry/boolDiff'
import * as groupStore from '../interaction/groupStore'
import { translateModel } from '../interaction/groupTranslate'
import {
  setGroupMoveAxis,
  setGroupMoveEnabled,
  setGroupRotateEnabled,
} from '../interaction/transformToolToggle'
import { geometryPure } from '../legacy/geometryBuilder'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { compatibleGroups, groupOf, nextGroupName } from '../geometry/modelGroups'
import { getModelOBB, transformToolsModule, type TransformToolsApi } from '../legacy/transformTools'
import { averageOpacityPercent } from '../geometry/modelOpacity'
import {
  commitGroupOpacity,
  commitRacksServersOpacity,
  previewGroupOpacity,
  previewRacksServersOpacity,
  racksServersSliderPercent,
  racksWithServers,
} from '../geometry/serverOpacity'
import * as store from '../projectStore'
import { minPoint } from '../interaction/selection'
import { fromDisplay, stepFor, toDisplay, unitConfig, unitOf } from '../lengthUnits'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { OpacitySlider } from './OpacitySlider'

/**
 * What the inspector shows when more than one model is selected.
 *
 * A port of the legacy's 多选操作 panel (STL_generate.html:10985). The single
 * -model inspector is the wrong thing here: a size box that wrote one number to
 * eight racks would be a foot-gun, so this panel offers only the operations
 * that are meaningful on a SET — move as a group, nudge by the group's minimum
 * corner, batch duplicate, batch delete.
 *
 * The minimum-corner anchor is the one to get right. The legacy caches it when
 * group move is enabled and translates every model by the DELTA against that
 * cache, so repeated edits accumulate rather than each one re-deriving a
 * different origin from the models' current positions.
 */

export interface MultiSelectPanelProps {
  project: CfdProject
  uids: string[]
  tools: TransformToolsApi | null
  onSelectionChange: (uids: string[], active: string | null) => void
}

export function MultiSelectPanel({ project, uids, tools, onSelectionChange }: MultiSelectPanelProps) {
  const { t } = useTranslation()
  const unit = unitOf(project)
  const step = stepFor(unit)
  const unitLabel = unitConfig(project).label
  const [, bump] = useState(0)
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])

  const models = uids
    .map((uid) => store.findConstruction(project, uid)?.model)
    .filter((m): m is Construction => !!m)
  if (models.length === 0) return null
  const serverRacks = racksWithServers(models)
  const serverRackUids = serverRacks.map((m) => m.uid)
  const serverSlider = racksServersSliderPercent(serverRacks)

  const moveOf = (m: Construction) =>
    (m as unknown as { moveTool?: { enabled?: boolean; axes?: Record<string, boolean> } }).moveTool
  const anyMove = models.some((m) => moveOf(m)?.enabled === true)
  const rotateOf = (m: Construction) =>
    (m as unknown as { rotateTool?: { enabled?: boolean } }).rotateTool
  const anyRotate = models.some((m) => rotateOf(m)?.enabled === true)
  const axes = moveOf(models[0]!)?.axes ?? { x: true, y: true, z: true }

  // From the BUILT bounds where they exist, because a model's world extent is
  // its meshes, not its anchor — a rack's anchor is its corner, an ACU's is
  // its centre, and mixing the two would move the group by the difference.
  const corner = minPoint(
    models.map((m) => {
      const root = (
        window as unknown as { __viewportRegistries?: { renderRootMap?: Map<string, unknown> } }
      ).__viewportRegistries?.renderRootMap?.get(m.uid)
      const world = root ? tools?.getRenderRootWorldBounds?.(root as never) : null
      if (world) return world as { min: { x: number; y: number; z: number } }
      // No built mesh (a pipe, a model on a tab that does not draw it): the
      // OBB from the project is the next best thing, and its centre-minus-half
      // is still a corner in the same space.
      const obb = getModelOBB(m)
      const corners = transformToolsModule().getOBBCorners(obb)
      return {
        min: {
          x: Math.min(...corners.map((c) => c.x)),
          y: Math.min(...corners.map((c) => c.y)),
          z: Math.min(...corners.map((c) => c.z)),
        },
      }
    }),
  )

  // The batch toggles live in `transformToolToggle`, shared with the R / V
  // shortcuts: a key and a click are the same call, one undo entry each.
  const setMoveEnabled = (on: boolean) => setGroupMoveEnabled(uids, on)
  const setRotateEnabled = (on: boolean) => setGroupRotateEnabled(uids, on)
  const setAxis = (axis: 'x' | 'y' | 'z', on: boolean) => setGroupMoveAxis(uids, axis, on)

  /** Translate the whole selection so its minimum corner lands on `value`. */
  /** `value` is in the PROJECT's unit — the box shows it that way. */
  function setCorner(axis: 'x' | 'y' | 'z', value: number) {
    if (!Number.isFinite(value)) return
    const delta = fromDisplay(value, unit) - corner[axis]
    if (!delta) return
    // Through `groupTranslate`, the same rule the gizmo drag uses: a pipe's
    // shape is its waypoints, not its anchor, and the two paths are the same
    // gesture. They used to answer differently — typing a number moved the
    // pipes, dragging did not.
    const step = { x: 0, y: 0, z: 0 }
    step[axis] = delta
    store.edit('setMultiMoveMinPoint', `${axis} += ${delta.toFixed(4)}`, (draft) => {
      for (const uid of uids) {
        translateModel(store.findConstruction(draft, uid)?.model, step)
      }
    })
  }

  function duplicateAll() {
    // One undo entry for the batch, as the legacy does — not one per model.
    const created = store.duplicateModels(uids)
    // The copies become the selection, so the next drag moves what was just
    // made rather than the originals underneath them.
    if (created.length) onSelectionChange(created, created[created.length - 1] ?? null)
  }

  function deleteAll() {
    // Confirmed, like the legacy: deleting eight models on a mis-click is not
    // something an undo entry alone makes acceptable.
    if (!window.confirm(t('confirm.batch_delete', { count: uids.length }))) return
    store.removeModels(uids)
    onSelectionChange([], null)
  }

  return (
    <div className="content" id="multi-select-panel">
      {/* The batch move and rotate, FIRST and in the single-model block's order
          — move toggle, rotate toggle, the axis locks, then the position
          (here the group's min point) — so they sit where the single-model
          panel has them. The hint moved under the toggles for the same
          reason. */}
      <div
        className="prop-group"
        id="multi-transform-group"
        style={{ borderLeftColor: '#1bbfd4' }}
      >
        <div className="prop-title" style={{ color: '#1bbfd4' }}>
          {t('inspector.multi_select_title', { count: models.length })}
        </div>
        <div className="prop-row">
          <label>{t('label.group_move')}</label>
          <button
            className={`btn frozen-ok${anyMove ? ' primary' : ''}`}
            id="multi-move-toggle"
            onClick={() => setMoveEnabled(!anyMove)}
          >
            {anyMove ? t('btn.stop_move') : t('btn.enable_move')}
          </button>
        </div>
        <div className="prop-row">
          <label>{t('label.group_rotate')}</label>
          <button
            className={`btn frozen-ok${anyRotate ? ' primary' : ''}`}
            id="multi-rotate-toggle"
            onClick={() => setRotateEnabled(!anyRotate)}
          >
            {anyRotate ? t('btn.stop_rotate') : t('btn.enable_rotate')}
          </button>
        </div>
        {anyMove && (
          <div className="prop-row">
            <label>{t('inspector.axis_constraint')}</label>
            <div style={{ display: 'flex', gap: 8, flex: 1, minWidth: 0, justifyContent: 'flex-end' }}>
              {(['x', 'y', 'z'] as const).map((axis) => (
                <label key={axis} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                  <input
                    id={`multi-move-axis-${axis}`}
                    type="checkbox"
                    checked={axes[axis] !== false}
                    onChange={(e) => setAxis(axis, e.target.checked)}
                    style={{ width: 'auto', margin: 0 }}
                  />
                  {axis.toUpperCase()}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="prop-hint">{t('inspector.multi_select_hint', { count: models.length })}</div>
      </div>

      {anyMove && (
        <div className="prop-group" style={{ borderLeftColor: 'var(--section-header-location)' }}>
          <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
            {t('inspector.min_point_anchor', { unit: unitLabel })}
          </div>
          <div className="prop-hint">{t('inspector.min_point_hint')}</div>
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div className="prop-row" key={axis}>
              <label>{t(`label.min_point_${axis}` as 'label.min_point_x')}</label>
              {/* In the project's unit, like every other length box: a
                  millimetre project showed its racks at 600 and this row at
                  0.6, and typing 1500 here moved the group 1.5 KILOMETRES.
                  The legacy renders it through `formatLength`/`getLengthStep`
                  (STL_generate.html:11005). */}
              <NumberField
                id={`multi-min-${axis}`}
                value={toDisplay(corner[axis], unit)}
                step={step}
                disabled={axes[axis] === false}
                onCommit={(v) => setCorner(axis, v)}
              />
            </div>
          ))}
        </div>
      )}

      {/* The selection's opacity as ONE slider, sitting at the group's MEAN —
          the legacy's avgOpacity row (STL_generate.html:11011). A drag is one
          undo entry for the whole group, which is what `previewMultiOpacity`
          buys over eight separate edits. The servers of the selected racks
          follow it (a select-all fades as a whole). */}
      <div className="prop-group">
        <div className="prop-title">{t('common.display')}</div>
        <div className="prop-row">
          <label htmlFor="multi-opacity-slider">{t('label.group_opacity')}</label>
          <OpacitySlider
            id="multi-opacity-slider"
            badgeId="multi-opacity-badge"
            committed={averageOpacityPercent(models)}
            onPreview={(percent) => previewGroupOpacity(uids, serverRackUids, percent)}
            onCommit={(percent) => commitGroupOpacity(uids, serverRackUids, percent)}
          />
        </div>
        {/* The servers alone, as in the one-rack inspector, so the cabinets can
            be faded and the servers brought back to look inside. */}
        {serverRacks.length > 0 && (
          <>
            <div className="prop-row">
              <label htmlFor="multi-server-opacity-slider">{t('label.multi_server_opacity')}</label>
              <OpacitySlider
                id="multi-server-opacity-slider"
                badgeId="multi-server-opacity-badge"
                committed={serverSlider.percent}
                onPreview={(percent) => previewRacksServersOpacity(serverRackUids, percent)}
                onCommit={(percent) => commitRacksServersOpacity(serverRackUids, percent)}
              />
            </div>
            <div className="prop-hint" id="multi-server-opacity-hint" data-mixed={serverSlider.mixed ? 'true' : 'false'}>
              {serverSlider.mixed ? `${t('rack.stacked_mixed')} — ` : ''}{t('hint.multi_server_opacity_desc')}
            </div>
          </>
        )}
      </div>

      <GroupSection models={models} onSelectionChange={onSelectionChange} />

      {models.length === 2 && <BoolDiffSection a={models[0]!} b={models[1]!} />}

      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          {t('inspector.batch_operations')}
        </div>
        <div className="prop-hint">{t('inspector.batch_hint')}</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="btn" id="multi-duplicate" onClick={duplicateAll}>
            <Icon name="copy" />{t('btn.batch_duplicate', { count: models.length })}
          </button>
          <button
            className="btn danger"
            id="multi-delete"
            onClick={deleteAll}
          >
            {t('btn.batch_delete', { count: models.length })}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * Naming a selection: create, rename, dissolve, or fold it into another group.
 *
 * A port of the legacy's Group block (STL_generate.html:11027). Two rules from
 * there decide when it appears at all:
 *
 * - **One kind only.** A group belongs to the category of its members, so a
 *   selection spanning racks and ACUs has no category to live in.
 * - **Two or more.** A group of one is dissolved on the next edit anyway.
 *
 * A group is not project data — it lives in `localStorage` beside the other
 * view preferences, in both apps — so nothing here dirties the .cfdproj.
 */
function GroupSection({
  models,
  onSelectionChange,
}: {
  models: Construction[]
  onSelectionChange: (uids: string[], active: string | null) => void
}) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => {
    // Subscribed before binding: the bind's first sync emits, and a listener
    // registered after it would miss the groups already in storage.
    const off = groupStore.subscribeGroups(() => bump((n) => n + 1))
    groupStore.bindGroupsToProject()
    return off
  }, [])

  const uids = models.map((m) => m.uid)
  const kinds = new Set(models.map((m) => String(m.kind ?? '')))
  if (kinds.size !== 1 || models.length < 2) return null
  const kind = String(models[0]!.kind ?? '')
  const groups = groupStore.getGroups()
  const own = groupOf(groups, uids[0]!)
  const allSame = !!own && uids.every((uid) => groupOf(groups, uid)?.id === own.id)
  const kindOf = (uid: string) => {
    const model = models.find((m) => m.uid === uid)
    if (model) return String(model.kind ?? '')
    const project = store.getProject()
    const hit = project ? store.findConstruction(project, uid)?.model : null
    return hit ? String(hit.kind ?? '') : null
  }
  const addable = compatibleGroups(groups, uids, kindOf, kind)

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--accent3, #00d4ff)' }}>
      {/* 'Group' is the legacy's own hard-coded title here — it has no
          dictionary key for this block's heading (STL_generate.html:11030). */}
      <div className="prop-title" style={{ color: 'var(--accent3, #00d4ff)' }}>
        Group
      </div>
      {allSame && own ? (
        <>
          <div className="prop-row">
            <label>{t('btn.group_name')}</label>
            <input
              id="multi-group-name"
              type="text"
              defaultValue={own.name}
              key={own.id + own.name}
              onBlur={(e) => {
                const name = e.target.value.trim()
                if (name) groupStore.renameModelGroup(own.id, name)
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </div>
          <button
            className="btn"
            id="multi-dissolve-group"
            onClick={() => groupStore.dissolveModelGroup(own.id)}
          >
            {t('btn.dissolve_group')}
          </button>
        </>
      ) : (
        <button
          className="btn primary"
          id="multi-create-group"
          onClick={() => {
            groupStore.createModelGroup(nextGroupName(groups, kind), uids)
            // The selection is left alone: the models are the same ones, and
            // the tree now draws them inside their new folder.
            onSelectionChange(uids, uids[uids.length - 1] ?? null)
          }}
        >
          {t('btn.create_group')}
        </button>
      )}
      {addable.length > 0 && (
        <div className="prop-row">
          <label>{t('btn.add_to_group')}</label>
          <select
            id="multi-add-to-group"
            value=""
            onChange={(e) => {
              if (e.target.value) groupStore.addModelsToGroup(e.target.value, uids)
            }}
          >
            <option value="">-- {t('btn.select_group')} --</option>
            {addable.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name} ({g.memberIds.length})
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  )
}

/**
 * Bool Diff, shown when exactly two models are selected.
 *
 * The TARGET is the surface that gets the hole and the TOOL is the thing
 * passing through it, so which is which matters — hence the swap button. Only
 * a baffle or a floor can be a target, and when neither selected model is one
 * the panel says so rather than offering a button that would refuse.
 */
function BoolDiffSection({ a, b }: { a: Construction; b: Construction }) {
  const { t } = useTranslation()
  const [swapped, setSwapped] = useState(false)
  const [keepTool, setKeepTool] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const target = swapped ? b : a
  const tool = swapped ? a : b
  const valid = canBeTarget(target)

  function run() {
    setError(null)
    const result = executeBoolDiff(viewportBoolDiffHost(), target.uid, tool.uid, keepTool)
    if (!result.ok) {
      setError(
        result.reason === 'no-overlap'
          ? t('error.bool_no_overlap')
          : result.reason === 'target-kind'
            ? t('error.bool_target_type')
            : t('error.bool_no_tool_bounds'),
      )
    }
  }

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--error-color)' }}>
      <div className="prop-title" style={{ color: 'var(--error-color)' }}>
        {t('bool.title')}
      </div>
      <div className="prop-row">
        <label>{t('bool.target')}</label>
        <span className="value" id="bool-target">
          {target.meta?.name ?? target.uid}
        </span>
      </div>
      <div className="prop-row">
        <label>{t('bool.tool')}</label>
        <span className="value" id="bool-tool">
          {tool.meta?.name ?? tool.uid}
        </span>
      </div>
      <div className="prop-row">
        <button className="btn" id="bool-swap" onClick={() => setSwapped((v) => !v)}>
          {t('bool.swap')}
        </button>
      </div>
      <div className="prop-row">
        <label>{t('bool.keep_tool')}</label>
        <input
          id="bool-keep-tool"
          type="checkbox"
          checked={keepTool}
          onChange={(e) => setKeepTool(e.target.checked)}
        />
      </div>
      {!valid && <div className="prop-hint">{t('bool.target_warning')}</div>}
      <button className="btn primary" id="bool-execute" disabled={!valid} onClick={run}>
        {t('bool.execute')}
      </button>
      {error && <div className="error">{error}</div>}
    </div>
  )
}

/**
 * The bounds and face transforms Bool Diff needs, read from the BUILT scene.
 *
 * A model's world extent is its meshes, not its anchor — a rack's anchor is a
 * corner and an ACU's is its centre — so the footprint has to come from what
 * was rendered.
 */
export function viewportBoolDiffHost(): BoolDiffHost {
  const w = window as unknown as {
    __viewportRegistries?: { renderRootMap?: Map<string, THREE.Object3D> }
    __transformTools?: TransformToolsApi
    __sceneBuilder?: { api?: { getFaceLayouts?: (m: unknown) => Record<string, FaceLayout> } }
  }
  const rootOf = (uid: string) => w.__viewportRegistries?.renderRootMap?.get(uid) ?? null
  const layoutOf = (uid: string, faceId: string): FaceLayout | null => {
    const project = store.getProject()
    const model = project ? store.findConstruction(project, uid)?.model : null
    if (!model) return null
    const layouts = geometryPure().getFaceLayouts(model) as Record<string, FaceLayout>
    return layouts?.[faceId] ?? null
  }
  return {
    worldBounds: (uid) => {
      const root = rootOf(uid)
      if (!root) return null
      root.updateMatrixWorld(true)
      return (w.__transformTools?.getRenderRootWorldBounds?.(root as never) ?? null) as
        | { min: { x: number; y: number; z: number }; max: { x: number; y: number; z: number } }
        | null
    },
    faceWorldMatrix: (uid, faceId) => {
      const root = rootOf(uid)
      const layout = layoutOf(uid, faceId)
      if (!root || !layout?.mat) return null
      root.updateMatrixWorld(true)
      return root.matrixWorld.clone().multiply(layout.mat)
    },
    faceSize: (uid, faceId) => {
      const layout = layoutOf(uid, faceId)
      return layout ? { w: layout.w, h: layout.h } : null
    },
  }
}

interface FaceLayout {
  mat: THREE.Matrix4
  w: number
  h: number
}
