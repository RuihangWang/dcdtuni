import type { CfdProject, Construction } from '../schema/cfdproj'
import { floorPolygon, polygonArea, polygonBBox, updateFloorPolygonBBox } from '../geometry/floorPolygon'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * A non-rectangular floor's bounding box.
 *
 * Shown INSTEAD of the generic size boxes, because on a polygon floor the size
 * is derived from the outline: typing into the generic box changes the reported
 * extent while the vertices stay put, and `case_04_polygon` and
 * `case_05_triangle` then export a floor whose shape and whose box disagree.
 * These two boxes rescale the outline — see `floorPolygon.ts`.
 *
 * Ported from the polygon branch of `renderWallBaffleInspector`
 * (STL_generate.html:8377).
 */

export interface FloorPolygonSectionProps {
  model: Construction
  project: CfdProject | null
}

export function FloorPolygonSection({ model, project }: FloorPolygonSectionProps) {
  const { t } = useTranslation()
  const polygon = floorPolygon(model)
  if (!polygon) return null

  const unit = unitOf(project)
  const step = stepFor(unit)
  const box = polygonBBox(polygon)
  // Area is a square measure, so it converts by the scale SQUARED — dividing by
  // the linear scale would report a 60 m² floor as 60000 mm².
  const scale = toDisplay(1, unit)
  const area = polygonArea(polygon) * scale * scale

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
      <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
        {t('inspector.size_title', { unit })}
      </div>
      <div className="prop-row">
        <label>{t('label.floor_shape')}</label>
        <span
          className="badge"
          id="floor-polygon-vertices"
          style={{ borderColor: 'rgba(0,212,255,0.4)', color: 'var(--accent3)' }}
        >
          {t('label.polygon_vertices', { count: polygon.length })}
        </span>
      </div>
      <div className="prop-row">
        <label>{t('label.floor_area')}</label>
        <span className="value" id="floor-polygon-area">
          {area.toFixed(4)} {unit}²
        </span>
      </div>
      <div className="prop-row">
        <label htmlFor="floor-bbox-w">{`${t('label.bbox_w')} (${unit})`}</label>
        <NumberField
          id="floor-bbox-w"
          value={toDisplay(box.width, unit)}
          step={step}
          decimals={4}
          min={0}
          onCommit={(v) => updateFloorPolygonBBox(model.uid, 'W', fromDisplay(v, unit))}
        />
      </div>
      <div className="prop-row">
        <label htmlFor="floor-bbox-d">{`${t('label.bbox_d')} (${unit})`}</label>
        <NumberField
          id="floor-bbox-d"
          value={toDisplay(box.depth, unit)}
          step={step}
          decimals={4}
          min={0}
          onCommit={(v) => updateFloorPolygonBBox(model.uid, 'D', fromDisplay(v, unit))}
        />
      </div>
      <div className="prop-hint">{t('hint.polygon_floor_edit')}</div>
    </div>
  )
}
