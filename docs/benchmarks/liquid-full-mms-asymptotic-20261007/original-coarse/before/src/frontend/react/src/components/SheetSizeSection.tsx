import type { CfdProject, Construction } from '../schema/cfdproj'
import { sheetSizeSpec } from '../geometry/sheetSizeFields'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import * as store from '../projectStore'
import { useTranslation } from '../useTranslation'
import { alertIfOutOfBounds } from '../ui/boundsAlert'
import { NumberField } from './NumberField'

/**
 * The size boxes for a ZERO-THICKNESS surface: a wall, a baffle, a rectangular
 * floor.
 *
 * These three are sheets. The SHARED renderer builds them from the two IN-PLANE
 * size components and nothing else (`renderFloorModel` and its wall and baffle
 * siblings, js/geometry_scene.js:1005), so the generic three-box group gives
 * them an editable box for an axis that is never drawn and never exported — the
 * number is written to the .cfdproj and changes nothing.
 *
 * The hint is the other half of the fix. A floor's HEIGHT comes from its anchor
 * Z, and a wall's thickness from the exporter, not from a size field — which is
 * the first thing someone looks for when a floor sits at the wrong level.
 *
 * Ports the `sizeHtml` branches of `renderWallBaffleInspector`
 * (STL_generate.html:8372-8395); the per-kind field table lives in
 * `geometry/sheetSizeFields.ts`.
 */

export interface SheetSizeSectionProps {
  project: CfdProject
  model: Construction
}

export function SheetSizeSection({ project, model }: SheetSizeSectionProps) {
  const { t } = useTranslation()
  const spec = sheetSizeSpec(model.kind as string | undefined)
  if (!spec) return null

  const unit = unitOf(project)
  const step = stepFor(unit)
  const size = (model.geometry as { size?: Record<string, number> } | undefined)?.size

  return (
    <div className="prop-group" id="sheet-size" style={{ borderLeftColor: 'var(--hint-color)' }}>
      <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
        {t(spec.title)}
      </div>
      {spec.fields.map((field) => (
        <div className="prop-row" key={field.axis}>
          <label htmlFor={`sheet-size-${field.axis}`}>
            {t(field.label)} ({unit})
          </label>
          <NumberField
            id={`sheet-size-${field.axis}`}
            value={toDisplay(size?.[field.axis] ?? 0, unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) =>
              alertIfOutOfBounds(
                store.setGeometryComponent(model.uid, 'size', field.axis, fromDisplay(v, unit)),
              )
            }
          />
        </div>
      ))}
      <div className="prop-hint">{t(spec.hint)}</div>
    </div>
  )
}
