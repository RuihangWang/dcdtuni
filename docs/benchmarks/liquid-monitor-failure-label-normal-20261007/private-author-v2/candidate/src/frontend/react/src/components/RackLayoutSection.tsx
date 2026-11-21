import type { CfdProject, Construction } from '../schema/cfdproj'
import { fromDisplay, stepFor, toDisplay, unitOf } from '../lengthUnits'
import {
  rackHeightMode,
  readRackSetting,
  readRackSize,
  updateRackHeightMode,
  updateRackSetting,
  updateRackSize,
  type RackHeightMode,
} from '../geometry/rackGeometry'
import { useTranslation } from '../useTranslation'
import { alertIfOutOfBounds } from '../ui/boundsAlert'
import { NumberField } from './NumberField'

/**
 * The rack's size and U-slot layout.
 *
 * This REPLACES the generic X/Y/Z size boxes for a rack, and that is the point:
 * those write `geometry.size` straight through, leaving the slot table, the
 * servers, the doors and the partitions describing the rack's previous shape.
 * Every control here goes through `syncRackModelState` instead — see
 * `rackGeometry.ts`.
 *
 * The height row is the pair that confuses people, so the legacy shows both
 * halves at once (STL_generate.html:9274): whichever of total height and unit
 * height the mode makes an INPUT is editable, and the other is shown disabled
 * as the value that was derived from it. A rack is 42U of 44.45 mm, or it is
 * 2.0 m tall — never both independently.
 */

export interface RackLayoutSectionProps {
  model: Construction
  project: CfdProject | null
}

export function RackLayoutSection({ model, project }: RackLayoutSectionProps) {
  const { t } = useTranslation()
  if (model.kind !== 'rack') return null

  const unit = unitOf(project)
  const step = stepFor(unit)
  const uid = model.uid
  const mode = rackHeightMode(model)
  const slot = readRackSetting(model, 'unitCount') ?? 42
  const slotHeight = readRackSetting(model, 'unitHeight') ?? 0.04445
  const widthRatio = readRackSetting(model, 'slotWidthRatio') ?? 0.8
  const totalHeight = readRackSize(model, 'H')

  return (
    <>
      <div className="prop-group" style={{ borderLeftColor: 'var(--hint-color)' }}>
        <div className="prop-title" style={{ color: 'var(--hint-color)' }}>
          {t('inspector.rack_size')}
        </div>
        <div className="prop-row">
          <label htmlFor="rack-size-w">{`${t('label.width_w')} (${unit})`}</label>
          <NumberField
            id="rack-size-w"
            value={toDisplay(readRackSize(model, 'W'), unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) => alertIfOutOfBounds(updateRackSize(uid, 'W', fromDisplay(v, unit)))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="rack-size-d">{`${t('label.depth_d')} (${unit})`}</label>
          <NumberField
            id="rack-size-d"
            value={toDisplay(readRackSize(model, 'D'), unit)}
            step={step}
            decimals={4}
            min={0}
            onCommit={(v) => alertIfOutOfBounds(updateRackSize(uid, 'D', fromDisplay(v, unit)))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="rack-height-mode">{t('label.height_mode')}</label>
          <select
            id="rack-height-mode"
            value={mode}
            onChange={(e) => updateRackHeightMode(uid, e.target.value as RackHeightMode)}
          >
            <option value="totalHeight">{t('label.height_mode_total')}</option>
            <option value="unitHeight">{t('label.height_mode_unit')}</option>
          </select>
        </div>
        {mode === 'totalHeight' ? (
          <div className="prop-row">
            <label htmlFor="rack-size-h">{`${t('label.total_height_h')} (${unit})`}</label>
            <NumberField
              id="rack-size-h"
              value={toDisplay(totalHeight, unit)}
              step={step}
              decimals={4}
              min={0}
              onCommit={(v) => alertIfOutOfBounds(updateRackSize(uid, 'H', fromDisplay(v, unit)))}
            />
          </div>
        ) : (
          <div className="prop-row">
            <label htmlFor="rack-unit-height">{`${t('label.unit_height')} (${unit})`}</label>
            <NumberField
              id="rack-unit-height"
              value={toDisplay(slotHeight, unit)}
              step={step}
              decimals={4}
              min={0}
              onCommit={(v) => updateRackSetting(uid, 'unitHeight', fromDisplay(v, unit))}
            />
          </div>
        )}
        <div className="prop-row">
          <label htmlFor="rack-unit-count">{t('label.unit_count')}</label>
          <NumberField
            id="rack-unit-count"
            value={slot}
            step={1}
            min={3}
            decimals={0}
            onCommit={(v) => updateRackSetting(uid, 'unitCount', Math.round(v))}
          />
        </div>
        {/* The derived half of the pair, shown disabled so the arithmetic is
            visible rather than something the user has to trust. */}
        {mode === 'unitHeight' ? (
          <div className="prop-row">
            <label htmlFor="rack-auto-total-height">{`${t('label.auto_total_height')} (${unit})`}</label>
            <input
              id="rack-auto-total-height"
              type="number"
              disabled
              value={toDisplay(totalHeight, unit).toFixed(4)}
              readOnly
            />
          </div>
        ) : (
          <div className="prop-row">
            <label htmlFor="rack-auto-unit-height">{`${t('label.auto_unit_height')} (${unit})`}</label>
            <input
              id="rack-auto-unit-height"
              type="number"
              disabled
              value={toDisplay(slotHeight, unit).toFixed(4)}
              readOnly
            />
          </div>
        )}
      </div>

      <div className="prop-group">
        <div className="prop-title">{t('inspector.uslot_layout')}</div>
        <div className="prop-row">
          <label htmlFor="rack-slot-width-ratio">{t('label.slot_width_ratio')}</label>
          <NumberField
            id="rack-slot-width-ratio"
            value={widthRatio}
            step={0.01}
            min={0.2}
            max={0.95}
            decimals={2}
            onCommit={(v) => updateRackSetting(uid, 'slotWidthRatio', v)}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="rack-safe-margin-top">{`${t('label.safe_margin_top')} (${unit})`}</label>
          <NumberField
            id="rack-safe-margin-top"
            value={toDisplay(readRackSetting(model, 'safeMarginTop') ?? 0, unit)}
            step={step}
            min={0}
            decimals={4}
            onCommit={(v) => updateRackSetting(uid, 'safeMarginTop', fromDisplay(v, unit))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="rack-safe-margin-bottom">{`${t('label.safe_margin_bottom')} (${unit})`}</label>
          <NumberField
            id="rack-safe-margin-bottom"
            value={toDisplay(readRackSetting(model, 'safeMarginBottom') ?? 0, unit)}
            step={step}
            min={0}
            decimals={4}
            onCommit={(v) => updateRackSetting(uid, 'safeMarginBottom', fromDisplay(v, unit))}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="rack-default-span">{t('label.default_span')}</label>
          <NumberField
            id="rack-default-span"
            value={readRackSetting(model, 'defaultSpanUnits') ?? 1}
            step={1}
            min={1}
            decimals={0}
            onCommit={(v) => updateRackSetting(uid, 'defaultSpanUnits', Math.round(v))}
          />
        </div>
      </div>
    </>
  )
}
