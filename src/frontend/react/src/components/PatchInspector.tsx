import { useEffect, useState } from 'react'
import { SeedInspector } from './SeedInspector'
import { acuPerformanceMode } from '../geometry/acuCoolingModel'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { AcuDrivingInputs } from './AcuCoolingModel'
import { stlNaming } from '../legacy/stlNaming'
import {
  addPatch,
  autoFillPatch,
  deletePatch,
  deletePatchWithCavity,
  resetPatchVector,
  togglePatchVectorVisibility,
  updatePatchCfd,
  updatePatchGeometry,
  type PatchActionResult,
} from '../geometry/patchActions'
import { fromDisplay, stepFor, toDisplay, unitOf, type LengthUnit } from '../lengthUnits'
import { PATCH_PALETTE } from '../geometry/patchGeometry'
import {
  findPatch,
  floorOpening,
  perforatedK,
  setFloorPatchReturn,
  updateFace,
  updateFloorOpening,
  updatePatch,
  updatePatchPhysics,
  type Perforation, PATCH_PHYSICS_DEFAULTS } from '../geometry/patchPhysics'
import { setFloorDefaultBoundaryType } from '../geometry/floorDefaultBc'
import { serverFaceExportName } from '../geometry/serverExportNames'
import { setWallRegionName, wallRegionName, wallRegionSolidName } from '../geometry/wallRegion'
import { deleteServerPatch, findServerPatch } from '../geometry/serverPatches'
import { ServerIdentityRows } from './ServerIdentityRows'
import type { TranslationKey } from '../i18n'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { patchAnchorKeys, patchAnchorWorld, worldToPatchAnchor } from '../geometry/patchAnchor'
import { clearFaceTexture, faceTextureAllowed, faceTextureSource, setFaceTexture } from '../geometry/faceTexture'
import { TextureFileDialog } from './TextureFileDialog'
import { clearPatchTexture, patchTexturePresetId, patchTexturePresets, patchTextureSource, setPatchTexture, setPatchTexturePreset } from '../geometry/patchTexture'
import { isWallMountedAcu } from '../geometry/acuUnitType'
import * as session from '../run/session'
import { readCasePath } from '../run/caseDir'
import { ServerPatchEditor } from './ServerPatchEditor'
import { KeyframeBadge } from '../transient/KeyframeBadge'

/**
 * The face and patch inspectors.
 *
 * This is where a boundary condition is actually set: an ACU patch's role and
 * flow, a baffle patch's perforation, a floor tile's opening, a heated wall
 * region's surface temperature — and, before any of that, where the opening is
 * CREATED at all. The tree can now select those nodes, and without these panels
 * the selection would show the model's own properties instead — the wrong
 * object, silently.
 *
 * Ports the per-kind branches of `renderPatchInspector`
 * (STL_generate.html:10664) and the face panel beside it.
 */

// The third member of the family, kept in its own file because it carries the
// seed panel and the opacity slider on top of everything the patch panel has.
// Re-exported here so a caller reaches all three surface panels — face, patch
// and region — from one import, the way `renderWallRegionInspector` sits beside
// `renderPatchInspector` in the legacy.
export { WallRegionInspector } from './WallRegionInspector'

type Bag = Record<string, unknown>

/**
 * Flash a warning for two seconds, the way the legacy `#safety-alert` does.
 *
 * A clamp changes the number the user just typed. Doing that silently is how
 * someone ends up believing a 5 m vent fits on a 0.6 m face, so the panel says
 * so — briefly, because the value on screen is already the corrected one.
 */
function useSafetyAlert(): [boolean, (result: PatchActionResult) => void] {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!shown) return
    const timer = setTimeout(() => setShown(false), 2000)
    return () => clearTimeout(timer)
  }, [shown])
  return [shown, (result: PatchActionResult) => setShown(!!result.clamped || !!result.error)]
}

export interface PatchInspectorProps {
  project: CfdProject
  model: Construction
  faceId: string
  patchId: string
}

export function PatchInspector({ project, model, faceId, patchId }: PatchInspectorProps) {
  const { t } = useTranslation()
  const [alerted, flash] = useSafetyAlert()
  const unit = unitOf(project)
  const faces = (model.geometry as Bag | undefined)?.faces as Record<string, Bag> | undefined
  const face = Array.isArray(faces) ? undefined : faces?.[faceId]
  const patch = ((face?.patches ?? []) as Bag[]).find((p) => p.id === patchId)
  // A rack's `geometry.faces` is an ARRAY of door entries, so the lookup above
  // can never resolve a SERVER patch — and the tree routes `serverPatch` nodes
  // here. Without this branch clicking one showed the bare hint below, which
  // reads as a broken panel rather than a patch that lives somewhere else.
  if (!patch && model.kind === 'rack') {
    return <ServerPatchPanel model={model} faceId={faceId} patchId={patchId} />
  }
  if (!patch) return <div className="prop-hint">{t('inspector.patch_editor')}</div>

  const naming = stlNaming()
  // The name the STL will carry. Shown read-only because it is DERIVED from the
  // model, the face and the patch — typing a name here that the exporter then
  // recomputes is how a boundary condition ends up on a patch nobody meant.
  const finalName = naming.getPatchFinalExportName(model as never, faceId, patch as never)
  const kind = model.kind

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          Patch Naming
        </div>
        <div className="prop-row">
          <label htmlFor="patch-name">Patch Name</label>
          <input
            id="patch-name"
            type="text"
            defaultValue={String(patch.name ?? '')}
            key={patchId}
            onBlur={(e) => {
              if (e.target.value !== patch.name) updatePatch(model.uid, patchId, 'name', e.target.value)
            }}
          />
        </div>
        <div className="prop-row">
          <label className="dim">Final STL Solid Name</label>
        </div>
        <div className="patch-final-name" id="patch-final-name">
          {finalName}
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#cc44ff' }}>
        <div className="prop-title" style={{ color: '#cc44ff' }}>
          {t('inspector.color_picker')}
        </div>
        <div className="color-swatches">
          {PATCH_PALETTE.map((color) => (
            <div
              key={color}
              className={`color-swatch${patch.color === color ? ' active' : ''}`}
              style={{ background: color }}
              onClick={() => updatePatch(model.uid, patchId, 'color', color)}
            />
          ))}
        </div>
      </div>

      {/* 2D projection — a rectangle or a circle, never both. */}
      <div className="prop-group" style={{ borderLeftColor: 'var(--highlight)' }}>
        <div className="prop-title" style={{ color: 'var(--highlight)' }}>
          {patch.type === 'circle' ? t('inspector.proj_2d_circle') : t('inspector.proj_2d_rect')}
        </div>
        <div className="prop-hint" id="patch-anchor-hint">
          {t('inspector.patch_anchor_hint')}
        </div>
        {patch.type === 'circle' ? (
          <>
            <PatchNumber unit={unit} label={t('label.circle_radius')} id="patch-r" value={patch.r} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'r', v))} />
            <PatchNumber unit={unit} label={t('label.proj_center_x')} id="patch-cx" value={patch.cx} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'cx', v))} />
            <PatchNumber unit={unit} label={t('label.proj_center_y')} id="patch-cy" value={patch.cy} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'cy', v))} />
          </>
        ) : (
          <>
            <PatchNumber unit={unit} label={t('label.cover_width')} id="patch-w" value={patch.w} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'w', v))} />
            <PatchNumber unit={unit} label={t('label.cover_height')} id="patch-h" value={patch.h} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'h', v))} />
            <PatchNumber unit={unit} label={t('label.corner_x')} id="patch-x" value={patch.x} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'x', v))} />
            <PatchNumber unit={unit} label={t('label.corner_y')} id="patch-y" value={patch.y} onCommit={(v) => flash(updatePatchGeometry(model.uid, patchId, 'y', v))} />
          </>
        )}
      </div>

      <PatchAnchorWorldGroup model={model} patchId={patchId} faceId={faceId} patch={patch} unit={unit} flash={flash} />

      {/* Fill is offered for rectangles only: a circle stretched to the face
          edges is not a circle, and the legacy refuses it the same way. */}
      {patch.type === 'rect' && kind !== 'custom' && (
        <div style={{ display: 'flex', gap: '8px', marginBottom: '15px' }}>
          <button
            className="btn"
            id="patch-fill-h"
            style={{ margin: 0 }}
            onClick={() => flash(autoFillPatch(model.uid, patchId, 'H'))}
          >
            {t('btn.fill_horizontal')}
          </button>
          <button
            className="btn"
            id="patch-fill-v"
            style={{ margin: 0 }}
            onClick={() => flash(autoFillPatch(model.uid, patchId, 'V'))}
          >
            {t('btn.fill_vertical')}
          </button>
        </div>
      )}

      <div
        id="safety-alert"
        className="alert"
        style={{
          display: alerted ? 'block' : 'none',
          margin: '0 0 12px',
          padding: '6px 8px',
          fontSize: '0.78rem',
          color: 'var(--error-color, #e06c75)',
          border: '1px solid rgba(224,108,117,0.4)',
          background: 'rgba(224,108,117,0.08)',
        }}
      >
        {patch.type === 'circle' ? t('error.safety_10pct') : t('error.value_out_of_range')}
      </div>

      {kind === 'custom' && (
        <ProjectionVector model={model} faceId={faceId} patchId={patchId} patch={patch} unit={unit} />
      )}

      {kind === 'acu' && <AcuPatchPhysics model={model} patchId={patchId} patch={patch} />}
      {kind === 'baffle' && <BafflePatchPhysics model={model} patchId={patchId} patch={patch} />}
      {kind === 'floor' && (
        <FloorOpening project={project} model={model} patchId={patchId} />
      )}

      {kind === 'box' && <BoxPatchRole model={model} patchId={patchId} patch={patch} />}
      {(kind === 'wall' || kind === 'custom' || kind === 'box') && (
        <PatchHeatProperties model={model} faceId={faceId} patchId={patchId} patch={patch} />
      )}

      {/* The face's own add buttons, repeated here.

          The tree gives walls, baffles and floors no face node — their patches
          hang straight off the model — so the face panel that normally carries
          these is unreachable for exactly the kinds that need the most openings.
          A raised floor is built one vent tile at a time, and having to walk back
          to a different panel between each is the difference between usable and
          not. */}
      <PatchAddPanel model={model} faceId={faceId} />

      {(kind === 'baffle' || kind === 'floor') && (
        <button
          className="btn"
          id="delete-patch-cavity"
          style={{ background: '#8b4513', marginBottom: '6px' }}
          onClick={() => deletePatchWithCavity(model.uid, patchId)}
        >
          {t('btn.delete_patch_cavity')}
        </button>
      )}
      <button
        className="btn danger"
        id="delete-patch"
        onClick={() => deletePatch(model.uid, patchId)}
      >
        {t('btn.delete_patch_destroy')}
      </button>
      {/* The legacy appends the seed panel at the END of the patch inspector
          (STL_generate.html:10827). It renders nothing unless this patch is a
          wall-baffle or perforated surface, which is the only case with a seed
          pair to place. */}
      <SeedInspector project={project} model={model} faceId={faceId} patchId={patchId} />
    </>
  )
}

/**
 * A patch on a rack SERVER's face, reached from the tree's `serverPatch` node.
 *
 * The same editor the face panel lists, shown on its own with the delete the
 * face panel puts in each row's header. `findServerPatch` resolves which server
 * owns the id — the tree node carries a face and a patch but no equipment, and
 * patch ids come from one project-wide counter, so the scan is unambiguous.
 *
 * Headed by the server's identity rows — its name and U-slot range — exactly as
 * the face panel is: a patch picked in the viewport otherwise opened a panel that
 * named neither the server nor where in the rack it sits.
 */
function ServerPatchPanel({
  model,
  faceId,
  patchId,
}: {
  model: Construction
  faceId: string
  patchId: string
}) {
  const { t } = useTranslation()
  const hit = findServerPatch(model, faceId, patchId)
  if (!hit) return <div className="prop-hint">{t('inspector.patch_editor')}</div>
  const { equipmentId, patch, index } = hit
  return (
    <>
      <ServerIdentityRows model={model} equipmentId={equipmentId} />
      <div className="prop-group" id="server-patch-inspector" style={{ borderLeftColor: '#0a8' }}>
        <div className="prop-title" style={{ color: '#0a8' }}>
          Patch Edit
        </div>
        <div className="prop-row">
          <label className="dim">Final STL Solid Name</label>
        </div>
        <div className="patch-final-name" id="patch-final-name">
          {serverFaceExportName(model, equipmentId, faceId, { isPatch: true, patchIndex: index })}
        </div>
        <ServerPatchEditor
          uid={model.uid}
          equipmentId={equipmentId}
          faceId={faceId}
          patch={patch}
        />
        <button
          className="btn danger"
          id="delete-patch"
          onClick={() => deleteServerPatch(model.uid, equipmentId, faceId, patchId)}
        >
          {t('btn.delete_patch_destroy')}
        </button>
      </div>
    </>
  )
}

/**
 * Where a new patch comes from: one button per shape.
 *
 * Exported because the panel belongs on any surface that can carry an opening,
 * and the legacy puts it in three separate places for that reason — the face
 * inspector for boxes and ACUs (STL_generate.html:10579), the model inspector
 * for walls, baffles and floors (:8402), and the partition-wall panel (:8982).
 */
export function PatchAddPanel({ model, faceId }: { model: Construction; faceId: string }) {
  const { t } = useTranslation()
  const isCustom = model.kind === 'custom'
  // A rack's openings are U-slots derived from the equipment in it. A free patch
  // on a rack face would export a boundary the slot geometry knows nothing about,
  // so the legacy `addPatch` returns early for racks and there is no panel.
  if (model.kind === 'rack') return null
  // A partition wall's holes go right through a 50 mm plate, so only the two
  // large faces describe one; the edges have nothing to punch.
  const editable = model.kind !== 'partitionWall' || faceId === 'front' || faceId === 'back'

  return (
    <div className="prop-group" style={{ borderLeftColor: '#cc44ff' }}>
      <div className="prop-title" style={{ color: '#cc44ff' }}>
        {t('inspector.projection_panel')}
      </div>
      {editable ? (
        <>
          <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
            <button
              className="btn primary"
              id="add-circle-patch"
              style={{ flex: 1, margin: 0 }}
              onClick={() => addPatch(model.uid, faceId, 'circle')}
            >
              {isCustom ? t('btn.add_proj_circle_patch') : t('btn.add_circle_patch')}
            </button>
            <button
              className="btn"
              id="add-rect-patch"
              style={{
                flex: 1,
                margin: 0,
                background: 'rgba(0,212,255,0.07)',
                borderColor: 'rgba(0,212,255,0.35)',
                color: 'var(--accent3)',
              }}
              onClick={() => addPatch(model.uid, faceId, 'rect')}
            >
              {isCustom ? t('btn.add_proj_rect_patch') : t('btn.add_rect_patch')}
            </button>
          </div>
          <div className="prop-hint">
            {isCustom ? t('hint.custom_proj_patch') : t('hint.parametric_patch')}
          </div>
        </>
      ) : (
        <div className="prop-hint">{t('hint.partition_hole_front_back')}</div>
      )}
    </div>
  )
}

/**
 * The direction an imported-mesh patch is projected along.
 *
 * On a parametric box the answer is never in doubt — the patch lies on a flat
 * face and cuts straight in. On an imported STL the patch is a beam pushed
 * through a curved surface, so this vector decides WHERE the opening lands, and
 * a wrong one puts the boundary condition on a surface nobody meant. Hence the
 * arrow, and hence a way back to the face's own inward normal.
 */
function ProjectionVector({
  model,
  faceId,
  patchId,
  patch,
  unit,
}: {
  model: Construction
  faceId: string
  patchId: string
  patch: Bag
  unit: LengthUnit
}) {
  const { t } = useTranslation()
  // Once the boolean difference has been executed the beam is gone — it has
  // become solid regions — so moving its projection would describe a cut that
  // no longer exists. The legacy disables the whole group at that point.
  const locked = patch.applied === true
  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
        <div className="prop-title" style={{ color: 'var(--accent3)' }}>
          {t('inspector.proj_vector')}
        </div>
        <div className="prop-row">
          <label htmlFor="patch-show-vector">{t('label.show_arrow')}</label>
          <button
            className="btn"
            id="patch-show-vector"
            style={{ width: '110px', margin: 0 }}
            disabled={locked}
            onClick={() => togglePatchVectorVisibility(model.uid, patchId)}
          >
            {patch.showVector === false ? t('label.toggle_off') : t('label.toggle_on')}
          </button>
        </div>
        <PatchNumber
          unit={unit}
          label={t('label.vector_x')}
          id="patch-vx"
          value={patch.vx}
          disabled={locked}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'vx', v)}
        />
        <PatchNumber
          unit={unit}
          label={t('label.vector_y')}
          id="patch-vy"
          value={patch.vy}
          disabled={locked}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'vy', v)}
        />
        <PatchNumber
          unit={unit}
          label={t('label.vector_z')}
          id="patch-vz"
          value={patch.vz}
          disabled={locked}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'vz', v)}
        />
      </div>
      <button
        className="btn"
        id="patch-reset-vector"
        disabled={locked}
        title={faceId}
        onClick={() => resetPatchVector(model.uid, patchId)}
      >
        {t('btn.reset_proj_default')}
      </button>
    </>
  )
}

/**
 * A fixed surface temperature on one patch of a wall, box or imported mesh.
 *
 * This is the only way to describe a heated or chilled REGION of a surface:
 * the model-level heat source applies to the whole body, and a hot pipe run
 * across a wall is not the whole wall. The checkbox is the switch the exporter
 * reads — `js/cfdrun_build.js:637` emits a heat patch only when
 * `fixedTemperature != null` — so turning it off has to remove the field, not
 * zero it.
 */
function PatchHeatProperties({
  model,
  faceId,
  patchId,
  patch,
}: {
  model: Construction
  faceId: string
  patchId: string
  patch: Bag
}) {
  const { t } = useTranslation()
  const cfd = (patch.cfd ?? {}) as Bag
  const stored = cfd.fixedTemperature
  const enabled = stored != null
  // 35 °C is the legacy's starting point, and the LAST value is kept while the
  // box is ticked off — re-ticking should give back what was there, not a
  // default that quietly changes the boundary condition.
  const temperature = enabled ? Number(stored) : 35.0

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
      <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
        {t('inspector.heat_properties')}
      </div>
      <div className="prop-row">
        <label htmlFor="patch-fixed-temp-enable">{t('label.enable_fixed_temp')}</label>
        <input
          type="checkbox"
          id="patch-fixed-temp-enable"
          checked={enabled}
          onChange={(e) =>
            updatePatchCfd(
              model.uid,
              faceId,
              patchId,
              'fixedTemperature',
              e.target.checked ? temperature : null,
            )
          }
        />
      </div>
      {enabled && (
        <>
          <div className="prop-row">
            <label htmlFor="patch-fixed-temp">{t('label.surface_temp_c')}</label>
            <NumberField
              id="patch-fixed-temp"
              value={temperature}
              step={0.5}
              onCommit={(v) => {
                // The result is a `PatchActionResult`, not a refusal flag —
                // discarded so `NumberField` does not read it as one and put
                // the old temperature back.
                updatePatchCfd(model.uid, faceId, patchId, 'fixedTemperature', v)
              }}
            />
          </div>
          <div className="prop-hint">{t('hint.fixed_value_t')}</div>
        </>
      )}
    </div>
  )
}

/**
 * One geometry box, typed in the project's input unit.
 *
 * The project stores metres and the box shows millimetres by default, because
 * that is what a rack or a floor-tile datasheet is written in — and because the
 * legacy converts every one of these fields the same way (`PATCH_LENGTH_KEYS`,
 * STL_generate.html:1421). Showing raw metres here would mean a user copying
 * `600` off a drawing gets a 600 metre opening.
 */
/**
 * The anchor in WORLD coordinates, editable.
 *
 * The 2D group above is the stored truth (face u/v); this one is the same
 * point seen from the room. An edit on any axis is converted back to u/v and
 * written through the same `updatePatchGeometry`, so the stored shape and
 * every existing file stay as they are.
 */
function PatchAnchorWorldGroup({
  model,
  patchId,
  faceId,
  patch,
  unit,
  flash,
}: {
  model: Construction
  patchId: string
  faceId: string
  patch: Bag
  unit: LengthUnit
  flash: (r: { clamped?: boolean; ok: boolean }) => void
}) {
  const { t } = useTranslation()
  const world = patchAnchorWorld(model, faceId, patch)
  if (!world) return null
  const commitAxis = (axis: 'x' | 'y' | 'z') => (v: number) => {
    const next = { ...world, [axis]: v }
    const uv = worldToPatchAnchor(model, faceId, next)
    if (!uv) return
    const keys = patchAnchorKeys(patch)
    const a = updatePatchGeometry(model.uid, patchId, keys.u, uv.u)
    const b = updatePatchGeometry(model.uid, patchId, keys.v, uv.v)
    flash({ ok: a.ok && b.ok, clamped: !!(a.clamped || b.clamped) })
  }
  return (
    <div className="prop-group" id="patch-anchor-world" style={{ borderLeftColor: '#ff4d4f' }}>
      <div className="prop-title" style={{ color: '#ff4d4f' }}>
        {t('inspector.patch_anchor_global')}
      </div>
      <div className="prop-hint">{t('inspector.patch_anchor_world_hint')}</div>
      <PatchNumber unit={unit} label={t('label.anchor_world_x')} id="patch-anchor-wx" value={world.x} onCommit={commitAxis('x')} />
      <PatchNumber unit={unit} label={t('label.anchor_world_y')} id="patch-anchor-wy" value={world.y} onCommit={commitAxis('y')} />
      <PatchNumber unit={unit} label={t('label.anchor_world_z')} id="patch-anchor-wz" value={world.z} onCommit={commitAxis('z')} />
    </div>
  )
}

function PatchNumber({
  label,
  id,
  value,
  unit,
  onCommit,
  disabled,
}: {
  label: string
  id: string
  value: unknown
  unit: LengthUnit
  onCommit: (v: number) => void
  disabled?: boolean
}) {
  return (
    <div className="prop-row">
      <label htmlFor={id}>
        {label} ({unit})
      </label>
      <NumberField
        id={id}
        value={toDisplay(typeof value === 'number' ? value : 0, unit)}
        step={stepFor(unit)}
        // 4 decimals, as the legacy's `formatLength` prints. At mm scale a
        // patch corner is 1200.0000 there, and trimming it makes the same
        // field read differently in the two apps.
        decimals={4}
        disabled={disabled}
        onCommit={(v) => onCommit(fromDisplay(v, unit))}
      />
    </div>
  )
}

/**
 * A box patch as an ACU return grille.
 *
 * An odd-shaped unit is modelled as an ACU plus a box against it; the return
 * openings sit on the box. Its patch takes role 'return' and a flow, and the
 * exporter emits it as a return-only ACU entry (cfdrun_build.js, `boxReturnPatches`).
 */
function BoxPatchRole({ model, patchId, patch }: { model: Construction; patchId: string; patch: Bag }) {
  const { t } = useTranslation()
  const physics = (patch.physics ?? {}) as Bag
  const role = String(physics.role ?? 'wall')
  const flow = Number(physics.volumeFlowRate ?? 1)
  const set = (field: string, value: unknown) =>
    updatePatchPhysics(model.uid, patchId, field, value, { role: 'wall', volumeFlowRate: 1, velocity: 2 })
  return (
    <div className="prop-group" style={{ borderLeftColor: role === 'return' ? '#e06c75' : '#7d8590' }}>
      <div className="prop-title">{t('label.box_patch_role')}</div>
      <div className="prop-row">
        <label htmlFor="box-patch-role">{t('label.patch_role')}</label>
        <select id="box-patch-role" value={role} onChange={(e) => set('role', e.target.value)}>
          <option value="wall">{t('label.role_wall')}</option>
          <option value="return">{t('label.role_return')}</option>
        </select>
      </div>
      {role === 'return' && (
        <>
          <div className="prop-row">
            <label htmlFor="box-patch-flow">{t('label.volume_flow_rate')}</label>
            <NumberField
              id="box-patch-flow"
              value={flow}
              step={0.1}
              min={0}
              decimals={3}
              onCommit={(v) => set('volumeFlowRate', v)}
            />
          </div>
          <div className="prop-hint">{t('hint.box_return_desc')}</div>
        </>
      )}
    </div>
  )
}

/** ACU patch: supply, return or wall — and what the supply blows. */
function AcuPatchPhysics({
  model,
  patchId,
  patch,
}: {
  model: Construction
  patchId: string
  patch: Bag
}) {
  const { t } = useTranslation()
  const physics = (patch.physics ?? {}) as Bag
  const role = String(physics.role ?? 'wall')
  const mode = acuPerformanceMode(model)
  const defaults = { ...PATCH_PHYSICS_DEFAULTS }
  const set = (field: string, value: unknown) =>
    updatePatchPhysics(model.uid, patchId, field, value, defaults)
  const roleColor = role === 'supply' ? '#4daafc' : role === 'return' ? '#e06c75' : '#7d8590'

  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('inspector.acu_patch_physics')}
      </div>
      <div style={{ borderLeft: `3px solid ${roleColor}`, padding: '8px' }}>
        <div className="prop-row">
          <label htmlFor="acu-patch-role">{t('label.patch_role')}</label>
          <select
            id="acu-patch-role"
            value={role}
            onChange={(e) => set('role', e.target.value)}
          >
            <option value="wall">{t('label.role_wall')}</option>
            <option value="supply">{t('label.role_supply')}</option>
            <option value="return">{t('label.role_return')}</option>
          </select>
        </div>
        {role === 'supply' && (
          <>
            {/* The supply temperature is what `fixedTemp` blows; under a
                capacity model the unit works out its own supply temperature and
                the set point below replaces this box, exactly as the legacy
                swaps them (STL_generate.html:8662-8669). */}
            {mode === 'fixedTemp' && (
              <div className="prop-row kf-field">
                <label htmlFor="acu-patch-temp">{t('label.supply_temp')}<KeyframeBadge modelUid={model.uid} param="acu.supplyTemp" target={patchId} /></label>
                <NumberField
                  id="acu-patch-temp"
                  value={Number(physics.temperature ?? 15.0)}
                  step={0.5}
                  onCommit={(v) => set('temperature', v)}
                />
              </div>
            )}
            <AcuDrivingInputs
              model={model}
              patchId={patchId}
              physics={physics}
              idPrefix="acu-patch"
            />
            <div className="prop-row kf-field">
              <label htmlFor="acu-patch-flow">{t('label.volume_flow_rate')}<KeyframeBadge modelUid={model.uid} param="acu.airflow" target={patchId} /></label>
              <NumberField
                id="acu-patch-flow"
                value={Number(physics.volumeFlowRate ?? 4.0)}
                step={0.1}
                onCommit={(v) => set('volumeFlowRate', v)}
              />
            </div>
          </>
        )}
        {role === 'supply' && isWallMountedAcu(model) && (
          <div className="prop-row kf-field">
            <label htmlFor="acu-patch-angle">{t('label.supply_angle')}<KeyframeBadge modelUid={model.uid} param="acu.supplyAngle" target={patchId} /></label>
            <NumberField
              id="acu-patch-angle"
              value={Number(physics.supplyAngleDeg ?? 0)}
              step={5}
              min={-60}
              max={60}
              onCommit={(v) => set('supplyAngleDeg', v)}
            />
          </div>
        )}
        {role === 'return' && (
          // Read-only on purpose: the return flow MIRRORS the supply total. An
          // editable box here would let a user set an ACU that swallows more
          // than it blows, and the case would not conserve mass.
          <div className="prop-row" style={{ opacity: 0.6 }}>
            <label>{t('label.volume_flow_rate')}</label>
            <input type="number" value={Number(physics.volumeFlowRate ?? 0)} disabled title={t('hint.return_mirrors_supply')} />
          </div>
        )}
        {(role === 'supply' || role === 'return') && (
          <PatchTextureRow model={model} patchId={patchId} patch={patch} role={role} />
        )}
      </div>
      <div className="prop-hint">{t('hint.acu_bc_desc')}</div>
    </div>
  )
}

/** Baffle patch: a wall, or a perforated plate with its own K. */
function BafflePatchPhysics({
  model,
  patchId,
  patch,
}: {
  model: Construction
  patchId: string
  patch: Bag
}) {
  const { t } = useTranslation()
  const physics = (patch.physics ?? {}) as Bag
  const baffleType = String(physics.baffleType ?? 'wall')
  const set = (field: string, value: unknown) =>
    updatePatchPhysics(model.uid, patchId, field, value, { baffleType: 'wall', porosity: 0 })

  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('inspector.patch_cfd_props')}
      </div>
      <div className="prop-row">
        <label htmlFor="baffle-patch-type">{t('label.baffle_type')}</label>
        <select id="baffle-patch-type" value={baffleType} onChange={(e) => set('baffleType', e.target.value)}>
          <option value="wall">{t('label.baffle_wall')}</option>
          <option value="perforated">{t('label.baffle_perforated')}</option>
        </select>
      </div>
      {baffleType === 'perforated' && (
        <PerforatedEditor
          perf={physics.perforated as Perforation | undefined}
          onChange={(field, value) => set(`perforated.${field}`, value)}
        />
      )}
    </div>
  )
}

/** Floor tile: wall, wall-baffle, or perforated. */
function FloorOpening({
  project,
  model,
  patchId,
}: {
  project: CfdProject
  model: Construction
  patchId: string
}) {
  const { t } = useTranslation()
  const opening = floorOpening(project, model.uid, patchId)
  // A ceiling RETURN grille: the patch is an ACU return outlet in the run,
  // which is a fourth choice next to the three tile physics — and exclusive
  // with them, so the exporter never sees one patch as both.
  const found = findPatch(project, model.uid, patchId)
  const physics = (found?.patch.physics ?? {}) as Bag
  const isReturn = physics.role === 'return'
  const type = isReturn ? 'return' : String(opening.type ?? 'wall')
  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('inspector.opening_properties')}
      </div>
      <div className="prop-row">
        <label className="dim" id="floor-opening-header">
          {t('label.patch_opening_settings')}
        </label>
      </div>
      <div className="prop-row">
        <label htmlFor="floor-opening-type">{t('label.opening_type')}</label>
        <select
          id="floor-opening-type"
          value={type}
          onChange={(e) => {
            const next = e.target.value
            if (next === 'return') {
              setFloorPatchReturn(model.uid, patchId, true)
              return
            }
            if (isReturn) setFloorPatchReturn(model.uid, patchId, false)
            updateFloorOpening(model.uid, patchId, 'type', next)
          }}
        >
          <option value="wall">{t('label.floor_wall')}</option>
          <option value="wallBaffle">{t('label.floor_wall_baffle')}</option>
          <option value="perforated">{t('label.floor_perforated')}</option>
          <option value="return">{t('label.floor_return')}</option>
        </select>
      </div>
      {isReturn && (
        <>
          <div className="prop-hint" id="floor-return-desc">{t('hint.floor_return')}</div>
          <div className="prop-row">
            <label htmlFor="floor-return-flow">{t('label.volume_flow_rate')}</label>
            <NumberField
              id="floor-return-flow"
              value={Number(physics.volumeFlowRate) || 0}
              step={0.05}
              min={0}
              onCommit={(v) => updatePatchPhysics(model.uid, patchId, 'volumeFlowRate', v)}
            />
          </div>
          <PatchTextureRow model={model} patchId={patchId} patch={found?.patch ?? {}} role="return" />
        </>
      )}
      {/* A tile's opening type is the same three-way physics choice the
          remaining area makes, on a patch instead of the sheet. */}
      {!isReturn && (
        <div className="prop-hint" id="floor-opening-desc">
          {bcDescription(type, 'region', t)}
        </div>
      )}
      {type === 'perforated' && (
        <PerforatedEditor
          perf={opening.perforation as Perforation | undefined}
          onChange={(field, value) => updateFloorOpening(model.uid, patchId, `perforation.${field}`, value)}
        />
      )}
    </div>
  )
}

/**
 * Hole shape, size, open area — and the K they imply.
 *
 * Exported because the rack DOOR is perforated in exactly the same way and used
 * to hand-roll two of these four fields, leaving a door with no hole shape and
 * no K readout — so every rack door in React was silently a circle-hole door,
 * and the shape reaches the .cfdrun and changes the door's pressure loss.
 *
 * `idPrefix` because two of these can be on screen at once (a rack's inlet and
 * outlet doors), and duplicate element ids would make a label point at the
 * wrong control and the harness address whichever came first.
 */
export function PerforatedEditor({
  perf,
  onChange,
  idPrefix = 'perf',
}: {
  perf: Perforation | undefined
  onChange: (field: string, value: unknown) => void
  idPrefix?: string
}) {
  const { t } = useTranslation()
  const shape = perf?.shape ?? 'circle'
  const diameter = Number.isFinite(perf?.holeDiameterMm) ? (perf?.holeDiameterMm as number) : 3.0
  const pct = Number.isFinite(perf?.openAreaPct) ? (perf?.openAreaPct as number) : 64.0
  return (
    <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
      <div className="prop-title" style={{ color: '#16825d' }}>
        {t('inspector.perforated_title')}
      </div>
      <div className="prop-row">
        <label htmlFor={`${idPrefix}-shape`}>{t('label.hole_shape')}</label>
        <select
          id={`${idPrefix}-shape`}
          value={shape}
          onChange={(e) => onChange('shape', e.target.value)}
        >
          <option value="circle">{t('label.hole_shape_circle')}</option>
          <option value="rect">{t('label.hole_shape_rect')}</option>
          <option value="diamond">{t('label.hole_shape_diamond')}</option>
          <option value="hexagon">{t('label.hole_shape_hexagon')}</option>
        </select>
      </div>
      <div className="prop-row">
        <label htmlFor={`${idPrefix}-diameter`}>{t('label.hole_diameter_mm')}</label>
        <NumberField
          id={`${idPrefix}-diameter`}
          value={diameter}
          step={0.1}
          min={0.1}
          onCommit={(v) => onChange('holeDiameterMm', v)}
        />
      </div>
      <div className="prop-row">
        <label htmlFor={`${idPrefix}-open-pct`}>{t('label.open_area_pct')}</label>
        <NumberField
          id={`${idPrefix}-open-pct`}
          value={pct}
          step={1}
          min={0.1}
          max={99.9}
          onCommit={(v) => onChange('openAreaPct', v)}
        />
      </div>
      <div className="prop-row">
        <label>{t('label.k_estimate')}</label>
        <span className="value" id={`${idPrefix}-k`}>
          {perforatedK({ shape, holeDiameterMm: diameter, openAreaPct: pct }).toFixed(2)}
        </span>
      </div>
      <div className="prop-hint">{t('hint.perforated_formula')}</div>
    </div>
  )
}

/** The face panel: what the whole surface is called and how it renders. */
/**
 * The part of the face no patch covers, and what it exports as.
 *
 * It is a region like any other — the exporter writes it as its own STL solid,
 * and the runner attaches a boundary condition to that solid by name — but the
 * only panel that could name one is reached through a wallRegion selection, and
 * BOTH routes to that require patches (the tree gates its region rows on
 * `patches.length > 0`, viewport picking on `facePatchCount > 0`). So a face
 * with nothing cut into it had no way to name the region it will export.
 *
 * The legacy's face panel carries this group unconditionally
 * (STL_generate.html:10548), which is why the name is here rather than only
 * appearing once the first patch is cut.
 */
function DefaultFaceRegion({ model, faceId }: { model: Construction; faceId: string }) {
  const name = wallRegionName(model, faceId, 0)
  const solidName = wallRegionSolidName(model, faceId, 0)
  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
      <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
        Default Face Region
      </div>
      <div className="prop-row">
        <label htmlFor="face-region-name">Patch Name</label>
        <input
          id="face-region-name"
          type="text"
          // Keyed on the face, so selecting another one reloads the box rather
          // than carrying this name over and writing it there on the next blur.
          key={`${model.uid}/${faceId}`}
          defaultValue={name}
          onBlur={(e) => {
            if (e.target.value !== name) setWallRegionName(model.uid, faceId, 0, e.target.value)
          }}
        />
      </div>
      <div className="prop-row">
        <label className="dim">Final STL Solid Name</label>
      </div>
      <div className="patch-final-name" id="face-region-final-name">
        {solidName}
      </div>
    </div>
  )
}

/**
 * The face's picture: a thumbnail, a load button that opens the file dialog,
 * and a clear button. Sits at the bottom of the face panel, as asked.
 */
/**
 * The grille / outlet style of a supply or return patch: a preset from the
 * shared registry, or a picture of the user's own through the texture dialog.
 */
export function PatchTextureRow({
  model,
  patchId,
  patch,
  role,
}: {
  model: Construction
  patchId: string
  patch: Bag
  role: string
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const presets = patchTexturePresets(role)
  const preset = patchTexturePresetId(patch as { texturePreset?: unknown })
  const src = patchTextureSource(patch)
  const custom = !!src && !preset
  return (
    <div id="patch-texture-row" style={{ marginTop: 6 }}>
      <div className="prop-row">
        <label htmlFor="patch-texture-preset">{t('label.patch_texture_style')}</label>
        <select
          id="patch-texture-preset"
          value={custom ? '__custom' : preset}
          onChange={(e) => {
            if (e.target.value === '__custom') return
            setPatchTexturePreset(model.uid, patchId, e.target.value)
          }}
        >
          <option value="">{t('texpreset.none')}</option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {t(p.labelKey as TranslationKey)}
            </option>
          ))}
          {custom && <option value="__custom">{t('texpreset.custom')}</option>}
        </select>
      </div>
      {src && (
        <div className="prop-row" style={{ justifyContent: 'center' }}>
          <img id="patch-texture-preview" src={src} alt="" style={{ maxWidth: '100%', maxHeight: 90, border: '1px solid var(--border)', borderRadius: 4 }} />
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn" id="patch-texture-load" style={{ margin: 0 }} onClick={() => setOpen(true)}>
          {t('btn.load_texture')}
        </button>
        {src && (
          <button className="btn" id="patch-texture-clear" style={{ margin: 0 }} onClick={() => clearPatchTexture(model.uid, patchId)}>
            {t('btn.clear_texture')}
          </button>
        )}
      </div>
      {open && (
        <TextureFileDialog
          startPath={readCasePath() || session.getServerInfo()?.caseDir || undefined}
          onPicked={(dataUrl) => {
            setPatchTexture(model.uid, patchId, dataUrl)
            setOpen(false)
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}

export function FaceTextureRow({ model, faceId }: { model: Construction; faceId: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (!faceTextureAllowed(model)) return null
  const src = faceTextureSource(model, faceId)
  return (
    <div className="prop-group" id="face-texture-group" style={{ borderLeftColor: '#c678dd' }}>
      <div className="prop-title" style={{ color: '#c678dd' }}>{t('label.face_texture')}</div>
      <div className="prop-hint">{t('hint.face_texture')}</div>
      {src && (
        <div className="prop-row" style={{ justifyContent: 'center' }}>
          <img
            id="face-texture-preview"
            src={src}
            alt=""
            style={{ maxWidth: '100%', maxHeight: 120, border: '1px solid var(--border)', borderRadius: 4 }}
          />
        </div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn" id="face-texture-load" style={{ margin: 0 }} onClick={() => setOpen(true)}>
          {t('btn.load_texture')}
        </button>
        {src && (
          <button className="btn" id="face-texture-clear" style={{ margin: 0 }} onClick={() => clearFaceTexture(model.uid, faceId)}>
            {t('btn.clear_texture')}
          </button>
        )}
      </div>
      {open && (
        <TextureFileDialog
          startPath={readCasePath() || session.getServerInfo()?.caseDir || undefined}
          onPicked={(dataUrl) => {
            setFaceTexture(model.uid, faceId, dataUrl)
            setOpen(false)
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  )
}

export function FaceInspector({
  model,
  faceId,
}: {
  model: Construction
  faceId: string
}) {
  const { t } = useTranslation()
  const faces = (model.geometry as Bag | undefined)?.faces as Record<string, Bag> | undefined
  const face = faces?.[faceId]
  if (!face) return <div className="prop-hint">{t('inspector.face_projection')}</div>
  const patches = (face.patches ?? []) as Bag[]
  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
      <div className="prop-title" style={{ color: 'var(--accent3)' }}>
        {t('inspector.face_projection')}
      </div>
      <div className="prop-row">
        <label htmlFor="face-display-name">{t('label.face_name')}</label>
        <input
          id="face-display-name"
          type="text"
          defaultValue={String(face.displayName ?? face.label ?? faceId)}
          key={`${model.uid}/${faceId}`}
          onBlur={(e) => updateFace(model.uid, faceId, 'displayName', e.target.value)}
        />
      </div>
      <div className="prop-row">
        <label htmlFor="face-opacity">{t('label.opacity')}</label>
        <NumberField
          id="face-opacity"
          value={Number(face.opacity ?? 1)}
          step={0.05}
          min={0}
          max={1}
          onCommit={(v) => updateFace(model.uid, faceId, 'opacity', v)}
        />
      </div>
      <div className="prop-row">
        <label htmlFor="face-reverse-normal">{t('preview.reverse_normal')}</label>
        <input
          type="checkbox"
          id="face-reverse-normal"
          checked={face.reverseSolidNormal === true}
          onChange={(e) => updateFace(model.uid, faceId, 'reverseSolidNormal', e.target.checked)}
        />
      </div>
      <div className="prop-row">
        <label>{t('inspector.patch_editor')}</label>
        <span className="value">{patches.length}</span>
      </div>
      <DefaultFaceRegion model={model} faceId={faceId} />
      <FaceTextureRow model={model} faceId={faceId} />
      {/* No `RemainingAreaBc` here. It renders only for a floor or a baffle,
          and neither ever reaches a FACE panel: `sheetChildren`
          (interaction/treeNodes.ts:220) emits no face row for a planar body and
          `resolveViewportSelection` (interaction/picking.ts:137) returns null
          when one is clicked. Mounted here it was unreachable code that read as
          the feature being present. It lives on the model inspector now
          (`SheetCfdSection`), which is where the legacy puts it. */}
      <PatchAddPanel model={model} faceId={faceId} />
    </div>
  )
}

/**
 * The boundary condition for the part of the surface no patch covers.
 *
 * On a raised floor that is most of the floor, and on a containment baffle most
 * of the baffle — so this is not a detail: without it a perforated floor can
 * only be described tile by tile, and the gaps between the tiles silently stay
 * solid wall.
 *
 * Mounted in TWO places, as the legacy mounts it: on the model inspector
 * (`SheetCfdSection`, from `renderFloorCfdInspector` STL_generate.html:8887 and
 * `renderBaffleCfdInspector` :8848), which is the only route while the sheet
 * has no patches; and on the wall-region panel (`renderWallRegionInspector`,
 * :10602), which appears once patches have split the sheet into regions.
 */
export function RemainingAreaBc({
  model,
  variant = 'region',
}: {
  model: Construction
  /**
   * Which of the legacy's two placements this is.
   *
   * `default` is the sheet's own CFD group (STL_generate.html:8899): it carries
   * the `label.remaining_region_default_bc` heading and the LONG explanation
   * (`hint.bc_*`). `region` is the wall-region panel (:10613), which is already
   * titled `inspector.region_cfd` and prints the short form
   * (`hint.patch_bc_*`). Defaulted to `region` because that caller passes no
   * variant; `SheetCfdSection` asks for `default` explicitly.
   */
  variant?: 'default' | 'region'
}) {
  const { t } = useTranslation()
  if (model.kind !== 'floor' && model.kind !== 'baffle') return null
  const cooling = ((model as unknown as { cooling?: Record<string, unknown> }).cooling ??
    {}) as Record<string, unknown>
  const isFloor = model.kind === 'floor'
  const type = String(
    (isFloor ? cooling.defaultBoundaryType : cooling.type ?? cooling.baffleType) ?? 'wall',
  )
  const perf = (isFloor ? cooling.defaultPerforation : cooling.perforation) as
    | Perforation
    | undefined
  const perfField = isFloor ? 'defaultPerforation' : 'perforation'

  return (
    <>
      {isFloor && variant === 'default' && (
        <div className="prop-row">
          <label className="dim" id="remaining-region-header">
            {t('label.remaining_region_default_bc')}
          </label>
        </div>
      )}
      <div className="prop-row">
        <label htmlFor="region-bc-type">
          {isFloor ? t('label.region_type') : t('label.baffle_type')}
        </label>
        <select
          id="region-bc-type"
          value={type}
          onChange={(e) =>
            isFloor
              ? // NOT a plain field write: two of the three types are INTERNAL
                // surfaces, and an internal surface cannot also be the boundary
                // the domain ends at. See `setFloorDefaultBoundaryType`.
                setFloorDefaultBoundaryType(model.uid, e.target.value)
              : store.setModelCooling(model.uid, 'type', e.target.value)
          }
        >
          <option value="wall">{isFloor ? t('label.floor_wall') : t('label.baffle_wall')}</option>
          {isFloor && <option value="wallBaffle">{t('label.floor_wall_baffle')}</option>}
          <option value="perforated">
            {isFloor ? t('label.floor_perforated') : t('label.baffle_perforated')}
          </option>
        </select>
      </div>
      {/* Which of the three the user picked decides whether snappyHexMesh
          deletes the mesh on one side, keeps both with a no-slip baffle, or
          emits a master/slave porousBafflePressure pair. Offered as three bare
          words, that is not a choice anyone can make. */}
      {isFloor && (
        <div className="prop-hint" id="region-bc-desc">
          {bcDescription(type, variant, t)}
        </div>
      )}
      {type === 'perforated' && (
        <PerforatedEditor
          perf={perf}
          onChange={(field, value) =>
            store.setModelCooling(model.uid, `${perfField}.${field}`, value)
          }
        />
      )}
    </>
  )
}

const BC_DESCRIPTION_KEYS: Record<'default' | 'region', Record<string, TranslationKey>> = {
  default: {
    wall: 'hint.bc_wall',
    wallBaffle: 'hint.bc_wall_baffle',
    perforated: 'hint.bc_perforated',
  },
  region: {
    wall: 'hint.patch_bc_wall',
    wallBaffle: 'hint.patch_bc_wall_baffle',
    perforated: 'hint.patch_bc_perforated',
  },
}

/** The line under a boundary-condition select, at the length the panel allows. */
function bcDescription(
  type: string,
  variant: 'default' | 'region',
  t: (key: TranslationKey) => string,
): string {
  const key = BC_DESCRIPTION_KEYS[variant][type]
  return key ? t(key) : ''
}
