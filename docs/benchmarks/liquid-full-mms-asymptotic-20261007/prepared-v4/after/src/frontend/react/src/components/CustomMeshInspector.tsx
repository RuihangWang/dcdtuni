import { useEffect, useState } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  addPatchToRegion,
  appliedFragmentCount,
  applyCustomPatchBoolDiff,
  canBoolDiff,
  unapplyCustomPatch,
} from '../geometry/customMesh'
import { fromDisplay, stepFor, toDisplay, unitOf, type LengthUnit } from '../lengthUnits'
import { locatePatch, updatePatchGeometry } from '../geometry/patchActions'
import * as store from '../projectStore'
import type { TreeSelection } from '../interaction/treeNodes'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { PatchInspector } from './PatchInspector'

/**
 * The panels an imported STL needs, which no parametric kind has.
 *
 * Two selections reach here and both are region-shaped:
 *
 * - **A solid region.** On a `custom` model the solids in the file are the
 *   OpenFOAM boundaries, so the region — not a face — is where a patch is added
 *   from. A port of `renderRegionInspector` (STL_generate.html:11404).
 * - **A patch on one.** The shared patch panel covers naming, colour and the
 *   projection vector, but a custom patch is a solid BEAM pushed through the
 *   mesh rather than a rectangle lying on a face, so it also has a body — a
 *   radius or a plate size, a length, a centre and an orientation — and a Bool
 *   Diff that turns the beam into cut regions. That is `renderPatchInspector`'s
 *   `kind === 'custom'` branch (STL_generate.html:10698).
 *
 * The beam's numbers LOCK once the diff is applied, exactly as the legacy
 * disables them: the beam no longer exists at that point, it has become solid
 * regions, so moving it would describe a cut that is not there. Un-apply first.
 */

type Bag = Record<string, unknown>

export interface CustomMeshInspectorProps {
  project: CfdProject
  model: Construction
  node: TreeSelection
}

export function CustomMeshInspector({ project, model, node }: CustomMeshInspectorProps) {
  if (node.type === 'region') {
    return <RegionInspector model={model} regionName={node.regionName ?? null} />
  }
  return (
    <>
      <PatchInspector
        project={project}
        model={model}
        faceId={node.faceId as string}
        patchId={node.patchId as string}
      />
      <CustomPatchBody project={project} model={model} patchId={node.patchId as string} />
    </>
  )
}

/**
 * One solid of an imported STL: what it is, and the two patches it can take.
 *
 * `regionName` null is the WHOLE model — a single-solid STL, or a mesh whose
 * file named no solids at all. It is a real target, not a missing one: its
 * patches project onto every region the beam reaches.
 */
export function RegionInspector({
  model,
  regionName,
}: {
  model: Construction
  regionName: string | null
}) {
  const { t } = useTranslation()
  const label = regionName ?? t('label.whole_model')
  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }} id="region-inspector">
        <div className="prop-title" style={{ color: 'var(--accent3)' }}>
          {t('inspector.original_solid_region')}
        </div>
        <div className="patch-final-name" id="region-name">
          {label}
        </div>
        <div className="prop-hint">{t('hint.region_patch_desc')}</div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#1bbfd4' }}>
        <div className="prop-title" style={{ color: '#1bbfd4' }}>
          {t('inspector.region_info')}
        </div>
        <div className="prop-row">
          <label>{t('inspector.mount_region')}</label>
          <span className="value" id="region-mount">
            {label}
          </span>
        </div>
        {/* The anchor ball is in the transform block above this panel, which
            the legacy region panel carries too (`anchorControlsForModel`,
            STL_generate.html:12757). */}
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#cc44ff' }}>
        <div className="prop-title" style={{ color: '#cc44ff' }}>
          {t('inspector.region_patch_ops')}
        </div>
        <button
          className="btn primary"
          id="region-add-circle"
          onClick={() => addPatchToRegion(model.uid, regionName, 'circle')}
        >
          {t('btn.add_proj_circle_patch_region')}
        </button>
        <button
          className="btn"
          id="region-add-rect"
          style={{ background: '#0e639c' }}
          onClick={() => addPatchToRegion(model.uid, regionName, 'rect')}
        >
          {t('btn.add_proj_rect_patch_region')}
        </button>
        <div className="prop-hint">{t('inspector.import_hint')}</div>
      </div>
    </>
  )
}

function LengthRow({
  label,
  id,
  value,
  unit,
  disabled,
  onCommit,
}: {
  label: string
  id: string
  value: unknown
  unit: LengthUnit
  disabled?: boolean
  onCommit: (v: number) => void
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
        decimals={4}
        disabled={disabled}
        onCommit={(v) => onCommit(fromDisplay(v, unit))}
      />
    </div>
  )
}

/**
 * Degrees, not metres.
 *
 * The orientation fields are the one place on this panel where the unit
 * selector must NOT apply — sending `rx` through `fromDisplay` at mm scale
 * would turn a 90 degree beam into 0.09.
 */
function AngleRow({
  label,
  id,
  value,
  disabled,
  onCommit,
}: {
  label: string
  id: string
  value: unknown
  disabled?: boolean
  onCommit: (v: number) => void
}) {
  return (
    <div className="prop-row">
      <label htmlFor={id}>{label}</label>
      <NumberField
        id={id}
        value={typeof value === 'number' ? value : 0}
        step={1}
        decimals={2}
        disabled={disabled}
        onCommit={onCommit}
      />
    </div>
  )
}

/** The beam itself, and the Bool Diff that turns it into cut regions. */
export function CustomPatchBody({
  project,
  model,
  patchId,
}: {
  project: CfdProject
  model: Construction
  patchId: string
}) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])
  // Cleared on every change of target, so a refusal from one patch is not still
  // on screen while a different one is selected.
  useEffect(() => setError(null), [model.uid, patchId])

  // Read through the STORE, not the `project` prop. The panel subscribes above
  // so that pressing Execute repaints its own status line and swaps in the
  // re-show button; re-reading the prop would hand back the pre-edit patch and
  // the button would keep saying "not executed" over an applied cut.
  const live = store.getProject() ?? project
  const found = locatePatch(live, model.uid, patchId)
  if (!found) return null
  const patch = found.patch as Bag
  const unit = unitOf(live)
  const applied = patch.applied === true
  const fragments = applied ? appliedFragmentCount(model.uid, patchId) : 0

  function run() {
    setError(null)
    const result = applyCustomPatchBoolDiff(model.uid, patchId)
    if (result.ok) return
    setError(
      result.reason === 'not-planar'
        ? t('error.custom_stl_not_planar')
        : t('error.custom_patch_no_intersection'),
    )
  }

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
        <div className="prop-title" style={{ color: 'var(--accent3)' }}>
          {t('inspector.mount_region')}
        </div>
        <div className="patch-final-name" id="patch-mount-region">
          {(patch.regionName as string) || t('label.whole_model')}
        </div>
        <div className="prop-hint">{t('hint.before_bool_diff')}</div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: 'var(--highlight)' }}>
        <div className="prop-title" style={{ color: 'var(--highlight)' }}>
          {t('inspector.patch_3d_body')}
        </div>
        {patch.type === 'circle' ? (
          <LengthRow
            label={t('label.cylinder_radius')}
            id="patch-body-r"
            value={patch.r}
            unit={unit}
            disabled={applied}
            onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'r', v)}
          />
        ) : (
          <>
            <LengthRow
              label={t('label.plate_width')}
              id="patch-body-w"
              value={patch.w}
              unit={unit}
              disabled={applied}
              onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'w', v)}
            />
            <LengthRow
              label={t('label.plate_height')}
              id="patch-body-h"
              value={patch.h}
              unit={unit}
              disabled={applied}
              onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'h', v)}
            />
          </>
        )}
        <LengthRow
          label={t('label.beam_length')}
          id="patch-body-depth"
          value={patch.depth}
          unit={unit}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'depth', v)}
        />
        <LengthRow
          label={t('label.center_x')}
          id="patch-body-px"
          value={patch.px}
          unit={unit}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'px', v)}
        />
        <LengthRow
          label={t('label.center_y')}
          id="patch-body-py"
          value={patch.py}
          unit={unit}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'py', v)}
        />
        <LengthRow
          label={t('label.center_z')}
          id="patch-body-pz"
          value={patch.pz}
          unit={unit}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'pz', v)}
        />
        <AngleRow
          label={t('label.orient_rx')}
          id="patch-body-rx"
          value={patch.rx}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'rx', v)}
        />
        <AngleRow
          label={t('label.orient_ry')}
          id="patch-body-ry"
          value={patch.ry}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'ry', v)}
        />
        <AngleRow
          label={t('label.orient_rz')}
          id="patch-body-rz"
          value={patch.rz}
          disabled={applied}
          onCommit={(v) => updatePatchGeometry(model.uid, patchId, 'rz', v)}
        />
      </div>

      <div className="prop-group" style={{ borderLeftColor: 'var(--accent2)' }}>
        <div className="prop-title" style={{ color: 'var(--accent2)' }}>
          {t('inspector.bool_diff_section')}
        </div>
        <div className="prop-hint" id="bool-diff-status">
          {t('bool.status_label')}
          <b style={{ color: applied ? '#98c379' : '#e5c07b' }}>
            {applied
              ? t('bool.generated_regions', { count: fragments })
              : t('bool.not_executed')}
          </b>
        </div>
        {applied ? (
          <button
            className="btn"
            id="patch-unapply-bool"
            onClick={() => unapplyCustomPatch(model.uid, patchId)}
          >
            {t('btn.reshow_beam')}
          </button>
        ) : (
          <button
            className="btn primary"
            id="patch-apply-bool"
            disabled={!canBoolDiff(model.uid)}
            onClick={run}
          >
            {t('btn.execute_bool_diff_op')}
          </button>
        )}
        {error && (
          <div className="error" id="bool-diff-error">
            {error}
          </div>
        )}
      </div>
    </>
  )
}
