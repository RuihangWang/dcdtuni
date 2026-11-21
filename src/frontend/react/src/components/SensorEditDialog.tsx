import { useState } from 'react'
import { createPortal } from 'react-dom'
import type { CfdProject } from '../schema/cfdproj'
import { unitOf } from '../lengthUnits'
import * as store from '../projectStore'
import {
  sensorUnitLabel,
  toSensorDisplay,
  validateSensorEdit,
  type SensorEditError,
} from '../run/sensorEdit'
import { useTranslation } from '../useTranslation'

/**
 * Rename a probe and correct its coordinates.
 *
 * A probe's name is not a label: it becomes the OpenFOAM `probes`
 * function-object name, the monitor legend entry and the key in the harvested
 * sensor JSON, so with no rename a whole result set is stuck at `sensor_1`,
 * `sensor_2`. And a mistyped coordinate can otherwise only be fixed by deleting
 * the probe and adding it again, or by finding and dragging its ball in 3D.
 *
 * The XYZ boxes are in the PROJECT's input unit, like every other geometry box:
 * in a room authored in millimetres, typing 3000 for a probe at 3 m has to mean
 * 3 m. The conversion and the three refusals live in `run/sensorEdit.ts`.
 *
 * Reached by DOUBLE-CLICKING the probe's name in the list, which is where the
 * legacy puts it too (STL_generate.html:23280) — the row is already crowded
 * with a checkbox, live coordinates, a reading and a delete button, and a sixth
 * control there would make the common case (glance at a temperature) worse to
 * serve the rare one.
 *
 * Ports `editSensorPointDialog` (STL_generate.html:23336).
 */

export interface SensorEditDialogProps {
  project: CfdProject | null
  probeId: string
  onClose: () => void
}

export function SensorEditDialog({ project, probeId, onClose }: SensorEditDialogProps) {
  const { t } = useTranslation()
  const probe = store.sensorProbes(project).find((p) => p.uid === probeId)
  const unit = unitOf(project)
  const location = probe?.geometry?.location ?? { x: 0, y: 0, z: 0 }
  const [name, setName] = useState(probe?.meta?.name ?? probeId)
  const [x, setX] = useState(String(toSensorDisplay(location.x, unit)))
  const [y, setY] = useState(String(toSensorDisplay(location.y, unit)))
  const [z, setZ] = useState(String(toSensorDisplay(location.z, unit)))
  const [error, setError] = useState<SensorEditError | null>(null)
  if (!probe) return null

  const label = sensorUnitLabel(project)

  function apply() {
    const result = validateSensorEdit(project, probeId, { name, x, y, z })
    if (!result.ok || !result.patch) {
      setError(result.error ?? 'sensor.err_coord_invalid')
      return
    }
    store.setSensorProbe(probeId, result.patch)
    onClose()
  }

  // Portalled to `body`, not rendered where it is written. The sensor panel
  // sits inside the right panel, which has a `backdrop-filter` — and that makes
  // the panel the containing block for `position: fixed`, so the "full-screen"
  // overlay laid itself out inside the panel instead: a dialog pinned to the
  // right edge, over a backdrop that dimmed only the panel behind it, with the
  // rest of the app still clickable. Every other dialog here portals for the
  // same reason (`PpEditDialogs`).
  return createPortal(
    // `.modal-overlay` / `.modal` are the export dialog's chrome, reused rather
    // than restyled: a second modal that looks different reads as a different
    // kind of thing. Clicking the backdrop cancels, as it does there.
    <div id="sensor-edit-overlay" className="modal-overlay" onClick={onClose}>
      <div className="modal sensor-edit-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{t('sensor.edit_title', { name: probe.meta?.name ?? probeId })}</div>
        <div className="prop-row">
          <label htmlFor="sensor-edit-name">{t('sensor.name')}</label>
          <input
            id="sensor-edit-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && apply()}
          />
        </div>
        {(
          [
            ['sensor-edit-x', x, setX],
            ['sensor-edit-y', y, setY],
            ['sensor-edit-z', z, setZ],
          ] as const
        ).map(([id, value, set]) => (
          <div className="prop-row" key={id}>
            <label htmlFor={id}>
              {id.slice(-1).toUpperCase()} ({label})
            </label>
            <input
              id={id}
              type="number"
              step={0.01}
              value={value}
              onChange={(e) => set(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && apply()}
            />
          </div>
        ))}
        {/* Kept in the dialog rather than shown as an alert: the box the user
            has to correct is right above it. */}
        <div className="error" id="sensor-edit-err">
          {error ? t(error) : ''}
        </div>
        <div className="modal-buttons">
          <button id="sensor-edit-cancel" className="btn" onClick={onClose}>
            {t('btn.cancel')}
          </button>
          <button id="sensor-edit-apply" className="btn primary" onClick={apply}>
            {t('btn.apply')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
