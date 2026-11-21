import { useEffect, useState } from 'react'
import type { CfdProject } from '../schema/cfdproj'
import * as store from '../projectStore'
import { k2display, setTempUnit, tempUnit, type TempUnit } from '../postprocess/ppUnits'
import { formatProbeCoord, sensorColor } from '../run/sensorSpheres'
import { fromSensorDisplay } from '../run/sensorEdit'
import { stepFor, unitOf, LENGTH_UNITS } from '../lengthUnits'
import {
  getSensorResults,
  openLatestSensorJson,
  subscribeSensorResults,
  toKelvin,
} from '../run/sensorResults'
import { getSolverMonitor, subscribeSolverMonitor } from '../run/solverMonitor'
import { useTranslation } from '../useTranslation'
import { NumberField } from './NumberField'
import { SensorEditDialog } from './SensorEditDialog'

/**
 * 传感器位点 — probe points the solver samples.
 *
 * Replicates `#solver-sensor-right` (STL_generate.html:1144): a temperature
 * unit, an XYZ entry row with +, the probe list, and the button that opens the
 * most recent sensor JSON.
 *
 * Probes live in `instrumentation.probes`, a dict keyed by id — NOT in
 * `constructions`. They are not geometry: nothing is meshed at a probe, the
 * solver only writes a value there, and putting them in constructions would put
 * them in the STL export.
 *
 * ## Where a reading comes from
 *
 * Two sources, and the newer one wins. While a solve runs, `solverMonitor`
 * polls `/api/probes-latest` and each probe's latest sample is its live
 * temperature. When the solve ends, `sensorResults` harvests the final row into
 * a JSON file and reads it back, which is also what 打开最近 sensor JSON opens.
 * Neither is stored in the project: a reading is a result, and writing it into
 * `.cfdproj` would dirty the document and land in the undo stack.
 *
 * ## Renaming
 *
 * Double-clicking a probe's name opens `SensorEditDialog`, the only way to
 * rename one or to type an exact coordinate. It is not a button because the row
 * already carries five controls and the name IS the affordance — the legacy
 * makes the same call (STL_generate.html:23280).
 */

export interface SensorPanelProps {
  project: CfdProject | null
}

export function SensorPanel({ project }: SensorPanelProps) {
  const { t } = useTranslation()
  const [x, setX] = useState(0)
  const [y, setY] = useState(0)
  const [z, setZ] = useState(0)
  const [unit, setUnit] = useState<TempUnit>(tempUnit())
  // The LENGTH unit, which is a different thing from `unit` above: that one is
  // the temperature the readings are shown in, this one is what the XYZ boxes
  // are typed in. Every other geometry box in the app follows it.
  const lengthUnit = unitOf(project)
  const unitLabel = LENGTH_UNITS[lengthUnit].label
  const unitStep = stepFor(lengthUnit)
  const [status, setStatus] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [, bump] = useState(0)
  const probes = store.sensorProbes(project)

  // Both stores push: the harvest lands seconds after a solve ends, and the
  // live poll every two seconds while one runs.
  useEffect(() => subscribeSensorResults(() => bump((n) => n + 1)), [])
  useEffect(() => subscribeSolverMonitor(() => bump((n) => n + 1)), [])

  const harvest = getSensorResults()
  const live = getSolverMonitor().data.sensors

  /** The probe's reading in KELVIN: the live curve if a solve is feeding one, else the harvested final. */
  function readingK(name: string): number | null {
    const samples = live[name]
    const last = samples?.[samples.length - 1]
    if (last) return last.value
    const row = harvest.readings.find((r) => r.name === name)
    // The harvested value is in the unit the FILE was written with, which is
    // whatever the temperature selector said at the time — not necessarily what
    // it says now, so it is converted back to Kelvin before display.
    return typeof row?.tFinal === 'number' ? toKelvin(row.tFinal, harvest.unit) : null
  }

  function openLatestJson() {
    const problem = openLatestSensorJson()
    setStatus(problem ? t('sensor.no_saved_json') : harvest.path ?? '')
  }

  return (
    <div id="solver-sensor-right">

      <div className="sm-section">
        <label htmlFor="sensor-temp-unit">{t('sensor.temp_unit')}</label>{' '}
        <select
          id="sensor-temp-unit"
          value={unit}
          onChange={(e) => {
            // Shared with the post-processing colorbar deliberately: a user who
            // set °C there does not expect Kelvin here.
            const next = e.target.value as TempUnit
            setTempUnit(next)
            setUnit(next)
          }}
        >
          <option value="K">K</option>
          <option value="C">°C</option>
          <option value="F">°F</option>
        </select>
      </div>

      <div className="sm-section">
        <div className="sensor-entry">
          <span>X</span>
          <NumberField id="sensor-input-x" value={x} step={unitStep} onCommit={setX} />
          <span>Y</span>
          <NumberField id="sensor-input-y" value={y} step={unitStep} onCommit={setY} />
          <span>Z</span>
          <NumberField id="sensor-input-z" value={z} step={unitStep} onCommit={setZ} />
          <span id="sensor-input-unit">{unitLabel}</span>
          <button
            id="sensor-add"
            title={t('sensor.add_tooltip')}
            disabled={!project}
            // `addSensorProbe` takes METRES; the boxes are in the project's
            // input unit, so on a mm project 3000 is three metres, not three
            // kilometres.
            onClick={() =>
              store.addSensorProbe(
                fromSensorDisplay(x, lengthUnit),
                fromSensorDisplay(y, lengthUnit),
                fromSensorDisplay(z, lengthUnit),
              )
            }
          >
            +
          </button>
        </div>
        <div className="prop-hint">{t('sensor.drag_hint')}</div>
      </div>

      <div className="sm-section" id="sensor-list">
        {probes.length === 0 ? (
          <div className="sm-empty">{t('sensor.header')}</div>
        ) : (
          probes.map((probe, index) => {
            const loc = probe.geometry?.location ?? { x: 0, y: 0, z: 0 }
            // The reading, when a solve has produced one, in the chosen unit.
            const value = readingK(probe.meta?.name ?? probe.uid)
            return (
              <div className="sensor-row" key={probe.uid} data-sensor={probe.uid}>
                {/* The colour this probe is everywhere else: its ball in the
                    viewport and its curve in the monitor, both indexed by
                    position in THIS list. Without it a rising curve named
                    `sensor_3` has to be matched to a ball by elimination.
                    Legacy row: dot, checkbox, name (STL_generate.html:23267). */}
                <span
                  data-sensor-dot={probe.uid}
                  title={probe.meta?.name ?? probe.uid}
                  style={{
                    display: 'inline-block',
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    flexShrink: 0,
                    background: sensorColor(index),
                  }}
                />
                <input
                  type="checkbox"
                  checked={probe.meta?.enabled !== false}
                  onChange={(e) => store.setSensorProbe(probe.uid, { enabled: e.target.checked })}
                />
                <span
                  className="sensor-name"
                  title={`${probe.meta?.name ?? probe.uid} (${t('sensor.dblclick_edit')})`}
                  onDoubleClick={() => setEditing(probe.uid)}
                >
                  {probe.meta?.name ?? probe.uid}
                </span>
                {/* The drag in the viewport writes this node directly, by
                    this attribute, exactly as the legacy does
                    (STL_generate.html:23293/23514). Going through React would
                    re-render per frame and rebuild the whole scene mid-drag. */}
                <span className="dim" data-sensor-coord-id={probe.uid}>
                  {formatProbeCoord(loc, lengthUnit)}
                </span>
                {typeof value === 'number' && (
                  <span className="value">{k2display(value).toFixed(1)}</span>
                )}
                <button
                  className="node-action-btn"
                  title={t('tooltip.delete')}
                  onClick={() => store.removeSensorProbe(probe.uid)}
                >
                  ✕
                </button>
              </div>
            )
          })
        )}
      </div>

      <div className="sm-section">
        <button id="sensor-open-json-btn" className="btn" onClick={openLatestJson}>
          {t('sensor.open_json')}
        </button>
        {/* The click's own message wins: a harvest error from a solve that
            wrote no probes must not hide the path the user just opened. */}
        {(status || harvest.error) && (
          <div className="prop-hint" id="sensor-json-status">
            {status || harvest.error}
          </div>
        )}
      </div>

      {editing && (
        <SensorEditDialog project={project} probeId={editing} onClose={() => setEditing(null)} />
      )}
    </div>
  )
}
