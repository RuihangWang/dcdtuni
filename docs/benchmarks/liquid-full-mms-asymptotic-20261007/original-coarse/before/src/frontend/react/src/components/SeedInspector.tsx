import { useEffect, useState } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import {
  isSeedHelperVisible,
  resetSeedConfig,
  setSeedDirectionComponent,
  setSeedOffset,
  subscribeSeedHelper,
  setActiveSeedSurface,
  toggleSeedHelperVisibility,
} from '../geometry/seedActions'
import {
  seedSurfaceForModel,
  seedSurfaceForPatch,
  seedSurfaceForWallRegion,
  type SeedSurface,
} from '../geometry/seedConfig'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The seed helper panel.
 *
 * A wall-baffle or perforated surface keeps mesh on both sides, so snappyHexMesh
 * has to be handed a point in each half. Those two points are placed
 * automatically at surface centre ± normal × offset, and when the automatic
 * placement lands in the wrong volume — inside the rack standing on a
 * perforated tile, or beyond a containment panel — the mesh comes out with the
 * wrong region and nothing in the solver log says so. This panel is the remedy:
 * read the anchor, turn the direction, push the pair apart, or put it back.
 *
 * Ported from `buildSeedInspectorHtml` (STL_generate.html:7468) with the same
 * i18n keys, and the same rule about WHERE it appears: only on a surface that
 * actually has a seed pair. A plain `wall` floor tile or an ACU opening is
 * single-sided, and offering the controls there would invite someone to set a
 * number the mesher never reads.
 *
 * The anchor is a READOUT. The legacy moves it by dragging the centre hub in the
 * 3D viewport, and there is no seed gizmo in this app yet — so showing an
 * editable box would promise an edit that cannot be committed to the plane
 * correctly from three separate keystrokes.
 */

export interface SeedInspectorProps {
  project: CfdProject | null
  model: Construction | null
  /**
   * The bucket the model came from. Optional, and only a fallback: `kind` is
   * what decides, and it is present on every model the store holds. It is
   * non-enumerable though, so a caller that has the bucket in hand should pass
   * it rather than rely on a clone having kept the discriminator.
   */
  bucket?: string
  faceId?: string
  patchId?: string
  wallIndex?: number
}

/** Which surface the current selection edits, mirroring `getCurrentEditableSeedSurfaceContext`. */
export function seedSurfaceForSelection(props: SeedInspectorProps): SeedSurface | null {
  const { project, model, faceId, patchId, wallIndex } = props
  if (!project || !model) return null
  const bucket = props.bucket ?? ''
  if (faceId && patchId) return seedSurfaceForPatch(project, model, bucket, faceId, patchId)
  if (faceId && Number.isInteger(wallIndex)) {
    return seedSurfaceForWallRegion(project, model, bucket, faceId, wallIndex as number)
  }
  return seedSurfaceForModel(project, model, bucket)
}

export function SeedInspector(props: SeedInspectorProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => subscribeSeedHelper(() => bump((n) => n + 1)), [])

  const surface = seedSurfaceForSelection(props)
  // Publish for the viewport's helper, and retract it when this panel goes —
  // the legacy draws the helper for the surface being edited and clears it on
  // any other selection (`updateSeedSurfaceHelper` starts with a clear).
  useEffect(() => {
    setActiveSeedSurface(surface)
    return () => setActiveSeedSurface(null)
  }, [surface])
  if (!surface) return null

  const unit = unitOf(props.project)
  const cfg = surface.seedConfig
  const helperVisible = isSeedHelperVisible()
  const fmt = (metres: number) => toDisplay(metres, unit).toFixed(3)

  return (
    <div className="prop-group" id="seed-inspector" style={{ borderLeftColor: '#5aa7ff' }}>
      <div className="prop-title" style={{ color: '#5aa7ff' }}>
        {t('inspector.seed_helper')}
      </div>
      <div className="prop-hint">{t('hint.seed_helper_desc')}</div>
      <div className="prop-row">
        <label>{t('label.current_state')}</label>
        <span
          className="badge"
          id="seed-status"
          style={{ borderColor: 'rgba(90,167,255,0.35)', color: '#5aa7ff' }}
        >
          {surface.isCustom ? t('label.seed_status_custom') : t('label.seed_status_default')}
        </span>
      </div>
      <div className="prop-row">
        <label>{t('label.show_seed_helper')}</label>
        <button
          className="btn frozen-ok"
          id="seed-helper-toggle"
          style={{ width: 110, margin: 0 }}
          onClick={() => toggleSeedHelperVisibility()}
        >
          {helperVisible ? t('label.toggle_on') : t('label.toggle_off')}
        </button>
      </div>
      <div className="prop-row">
        <label>{t('label.seed_anchor')}</label>
        <span
          className="badge"
          id="seed-anchor"
          style={{ width: 'auto', fontFamily: "'Share Tech Mono',monospace" }}
        >
          {fmt(cfg.anchor.x)}, {fmt(cfg.anchor.y)}, {fmt(cfg.anchor.z)}
        </span>
      </div>
      {(['x', 'y', 'z'] as const).map((axis) => (
        <div className="prop-row" key={axis}>
          <label>
            {t(axis === 'x' ? 'label.seed_direction_x' : axis === 'y' ? 'label.seed_direction_y' : 'label.seed_direction_z')}
          </label>
          <NumberField
            id={`seed-direction-${axis}`}
            value={cfg.direction[axis]}
            step={0.05}
            decimals={3}
            onCommit={(v) => setSeedDirectionComponent(surface, axis, v)}
          />
        </div>
      ))}
      <div className="prop-row">
        <label>{t('label.seed_offset')}</label>
        <NumberField
          id="seed-offset"
          value={toDisplay(cfg.offset, unit)}
          step={stepFor(unit)}
          min={toDisplay(surface.offsetMin, unit)}
          decimals={4}
          onCommit={(v) => setSeedOffset(surface, fromDisplay(v, unit))}
        />
      </div>
      <div className="prop-hint">
        {t('hint.seed_offset_min', { value: fmt(surface.offsetMin), unit })}
      </div>
      <button
        className="btn"
        id="seed-reset-default"
        style={{
          marginTop: 8,
          background: 'rgba(90,167,255,0.08)',
          borderColor: 'rgba(90,167,255,0.35)',
          color: '#5aa7ff',
        }}
        onClick={() => resetSeedConfig(surface)}
      >
        {t('btn.reset_seed_default')}
      </button>
    </div>
  )
}
