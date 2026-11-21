import { addPatchToRegion } from '../geometry/customMesh'
import { solidRegionsOf } from '../geometry/customRegions'
import { toDisplay, unitConfig, unitOf, type LengthUnit } from '../lengthUnits'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { useTranslation } from '../useTranslation'
import { RichText } from '../ui/richText'
import { NumberField } from './NumberField'

/**
 * The model panel for an IMPORTED mesh.
 *
 * A `custom` model's `geometry.size` is a MEASUREMENT, not a setting: it is the
 * bounding box of the triangles that came out of the STL file. The generic size
 * group renders it as three editable boxes, so typing in one claims to resize a
 * mesh that is never re-tessellated — the number changes and the geometry does
 * not. The legacy shows the same three values DISABLED for exactly that reason
 * (STL_generate.html:9029).
 *
 * The region count beside them is the number that decides how many OpenFOAM
 * patches the file exports as: each `solid` block in a multi-solid ASCII STL
 * becomes one boundary patch, so "3" and "30" are very different cases and the
 * panel said neither.
 *
 * The projection group is the MODEL-level one (`__whole__`, passed here as a
 * null region): it drops a cutter against the whole imported body rather than
 * against one detected solid, which is the only way in when the import came
 * back as a single unnamed region.
 */

export interface ImportedStlOverviewProps {
  project: CfdProject | null
  model: Construction
}

function sizeOf(model: Construction, axis: 'x' | 'y' | 'z', unit: LengthUnit): number {
  const size = (model.geometry as { size?: Record<string, number> } | undefined)?.size
  return toDisplay(Number(size?.[axis] ?? 0), unit)
}

export function ImportedStlOverview({ project, model }: ImportedStlOverviewProps) {
  const { t } = useTranslation()
  const unit = unitOf(project)
  const label = unitConfig(project).label
  const regions = solidRegionsOf(model)

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          {t('inspector.imported_stl_overview')}
        </div>
        {/* Disabled, not read-only text: the legacy keeps them as number boxes
            so the panel looks the same as a parametric model's, and disabling
            is what says the value cannot be typed over. */}
        <div className="prop-row">
          <label>{`${t('label.model_width_w')} (${label})`}</label>
          <NumberField id="custom-size-w" value={sizeOf(model, 'x', unit)} decimals={4} disabled onCommit={() => {}} />
        </div>
        <div className="prop-row">
          <label>{`${t('label.model_depth_d')} (${label})`}</label>
          <NumberField id="custom-size-d" value={sizeOf(model, 'z', unit)} decimals={4} disabled onCommit={() => {}} />
        </div>
        <div className="prop-row">
          <label>{`${t('label.model_height_h')} (${label})`}</label>
          <NumberField id="custom-size-h" value={sizeOf(model, 'y', unit)} decimals={4} disabled onCommit={() => {}} />
        </div>
        {/* The dictionary entry wraps the count in <b>. `RichText` parses that
            one tag rather than stripping it — this used to strip it here, which
            left the same markup showing raw on every OTHER consumer of a
            <b>-bearing key. */}
        <div className="prop-hint" id="custom-solid-regions-count">
          <RichText text={t('label.solid_regions_count', { count: regions.length })} />
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#cc44ff' }}>
        <div className="prop-title" style={{ color: '#cc44ff' }}>
          {t('inspector.projection_panel')}
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <button
            className="btn primary"
            id="custom-whole-circle-patch"
            style={{ flex: 1, margin: 0 }}
            onClick={() => addPatchToRegion(model.uid, null, 'circle')}
          >
            {t('btn.add_proj_circle_patch')}
          </button>
          <button
            className="btn"
            id="custom-whole-rect-patch"
            style={{
              flex: 1,
              margin: 0,
              background: 'rgba(0,212,255,0.07)',
              borderColor: 'rgba(0,212,255,0.35)',
              color: 'var(--accent3)',
            }}
            onClick={() => addPatchToRegion(model.uid, null, 'rect')}
          >
            {t('btn.add_proj_rect_patch')}
          </button>
        </div>
        <div className="prop-hint">{t('hint.custom_patch_desc')}</div>
      </div>
    </>
  )
}
