import type { CfdProject } from '../schema/cfdproj'
import {
  ROOM_TEMP_PRESETS,
  currentReadout,
  isPresetTemperature,
  presetLabel,
  roomTemperatureC,
  selectRoomTemperature,
  setRoomTemperature,
} from '../geometry/roomTemperature'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'

/**
 * The project panel's room-temperature group.
 *
 * A preset select for the seven values a data centre is actually run at, and a
 * number box that appears only once the value is none of them — the legacy's
 * `_renderRoomTempUI` (STL_generate.html:8264). The K and beta readout is part
 * of the control, not a nicety: beta = 1/TRef is what the buoyancy term uses,
 * so seeing it change is how a user knows the number reached the solver.
 */
export function RoomTemperature({ project }: { project: CfdProject | null }) {
  const { t } = useTranslation()
  const tempC = roomTemperatureC(project)
  const custom = !isPresetTemperature(tempC)

  return (
    <div className="prop-group" style={{ borderLeftColor: 'var(--accent3)' }}>
      <div className="prop-title" style={{ color: 'var(--accent3)' }}>
        {t('inspector.room_temp_title')}
      </div>
      <div className="prop-hint">{t('inspector.room_temp_desc')}</div>
      <div className="prop-row">
        <label htmlFor="room-temp-select">{t('inspector.room_temp_label')}</label>
        <select
          id="room-temp-select"
          style={{ flex: 1, minWidth: 120 }}
          value={custom ? 'custom' : String(tempC)}
          onChange={(e) => selectRoomTemperature(e.target.value)}
        >
          {ROOM_TEMP_PRESETS.map((preset) => (
            <option key={preset} value={preset}>
              {presetLabel(preset)}
            </option>
          ))}
          <option value="custom">{t('inspector.room_temp_custom')}</option>
        </select>
      </div>
      {custom && (
        <div className="prop-row">
          <label htmlFor="room-temp-custom">{t('inspector.room_temp_custom_input')}</label>
          <NumberField
            id="room-temp-custom"
            value={tempC}
            step={0.5}
            min={-50}
            max={100}
            width={80}
            onCommit={(v) => setRoomTemperature(v)}
          />
        </div>
      )}
      <div className="prop-hint" id="room-temp-current">
        {t('inspector.room_temp_current', currentReadout(tempC))}
      </div>
    </div>
  )
}
