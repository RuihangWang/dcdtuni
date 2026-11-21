import { useEffect, useRef, useState } from 'react'
import type { CfdProject, Construction } from '../schema/cfdproj'
import {
  clampWidthRatio,
  effectiveDepth,
  exitRackDetail,
  findDoor,
  getRackDetail,
  setCoolingField,
  setDoorEnabled,
  setDoorOffset,
  setDoorPerforation,
  setDoorRender,
  setInnerWidthMeters,
  setInnerWidthRatio,
  setServerDepthAbsolute,
  setShowContext,
  subscribeRackDetail,
  type ServerDepthResult,
} from '../geometry/rackDetail'
import { rackState } from '../legacy/cfdprojModules'
import {
  isLiquidCooled,
  manifoldView,
  setManifoldLayout,
  setManifoldMountFace,
  setManifoldParam,
} from '../geometry/rackManifold'
import { useTranslation } from '../useTranslation'
import type { Perforation } from '../geometry/patchPhysics'
import { NumberField } from './NumberField'
import { fromDisplay, LENGTH_UNITS, stepFor, toDisplay, unitOf } from '../lengthUnits'
import { PerforatedEditor } from './PatchInspector'

/**
 * The rack detail editor.
 *
 * Replaces the inspector while a rack is being edited in isolation, exactly as
 * the legacy `renderRackDetailInspector` does (STL_generate.html:9096). The
 * sliders are the reason this panel exists: mounting width and door setbacks
 * are ratios of the rack, and typing metres for them is guesswork.
 *
 * Slider semantics, copied deliberately:
 *
 * - **`input` mutates live, `change` commits.** The legacy keeps one
 *   `_rdCheckpointPushed` flag for the whole drag; here the checkpoint is
 *   pushed on the first `input` of a drag and cleared on `change`, which is one
 *   undo entry per drag rather than one per frame.
 * - **Percent ranges are of the RACK, not of each other.** Inlet and outlet
 *   both run 0-50% of the depth, so the pair can never swallow the rack.
 * - **The "both" slider writes both offsets**, and reads back the LARGER of the
 *   two, so it shows a sane value when they differ.
 */

export interface RackDetailPanelProps {
  project: CfdProject | null
  /** The rack being edited, resolved by the caller from the detail target. */
  rack: Construction | null
}

type RackGeom = {
  size?: { x?: number; y?: number; z?: number }
  slotWidthRatio?: number
  slot?: number
  slotHeight?: number
}

export function RackDetailPanel({ project, rack }: RackDetailPanelProps) {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => subscribeRackDetail(() => bump((n) => n + 1)), [])
  // One checkpoint per drag: set on the first `input`, cleared on `change`.
  const dragging = useRef(false)
  // The refusal a per-server depth write answered with, keyed by the server so
  // the message renders under the row that caused it.
  const [serverError, setServerError] = useState<{ uid: string; msg: string } | null>(null)

  const detail = getRackDetail()
  // Every length box here follows `meta.inputUnit`, as every other geometry box
  // in both apps does — the legacy labels this panel with
  // `getLengthUnitLabel()` and converts back in the setter (:9128, :11847). The
  // manifold's diameter and offset stay in mm: they are component specs, not
  // room geometry, and their fields are named `…Mm`.
  const unit = unitOf(project)
  const unitLabel = LENGTH_UNITS[unit].label
  const lengthStep = stepFor(unit)
  /** A read-only dimension, in the project's unit and at its precision. */
  const dim = (metres: number) => toDisplay(metres, unit).toFixed(unit === 'm' ? 3 : 0)
  if (!rack) return null

  const uid = rack.uid
  const g = (rack.geometry ?? {}) as RackGeom
  const width = Number(g.size?.x ?? 0.6)
  const depth = Number(g.size?.z ?? 1.2)
  const height = Number(g.size?.y ?? 2)
  const ratio = clampWidthRatio(Number(g.slotWidthRatio ?? 0.8))
  const slots = Number(g.slot ?? 42)
  // One decimal, as the legacy prints it: 1U is 44.45 mm and rounding to
  // 44 or 45 makes the two apps' panels disagree on the same rack.
  const unitMm = (Number(g.slotHeight ?? 0.04445) * 1000).toFixed(1)
  const withOffsets = rack as Construction & {
    inletOffset?: number
    outletOffset?: number
    cooling?: Record<string, unknown>
  }
  const inlet = Number(withOffsets.inletOffset ?? 0)
  const outlet = Number(withOffsets.outletOffset ?? 0)
  const cooling = (withOffsets.cooling ?? {}) as Record<string, unknown>
  const servers = ((rack.constructions as { servers?: Record<string, Record<string, unknown>> })
    ?.servers ?? {}) as Record<string, Record<string, unknown>>
  const serverList = Object.values(servers)
  const pct = (v: number) => (depth > 0 ? Math.round((v / depth) * 100) : 0)

  /** Start (or continue) a drag: check-point once, then mutate live. */
  function live(fn: () => void) {
    if (!dragging.current) {
      dragging.current = true
      // The live writers do not push, so the checkpoint has to come from here.
      import('../projectStore').then((store) => store.pushUndoCheckpoint('rackDetailDrag'))
    }
    fn()
  }
  const endDrag = () => {
    dragging.current = false
  }

  /** What a refused per-server depth write says under its row. */
  function depthErrorText(result: ServerDepthResult): string | null {
    if (result.ok) return null
    return result.error === 'overlap'
      ? t('error.server_depth_overlap')
      : result.error === 'range'
        ? t('error.server_depth_range')
        : null
  }

  const inletDoor = findDoor(rack, 'inlet')
  const outletDoor = findDoor(rack, 'outlet')

  return (
    <div id="rack-detail-panel">
      <div className="prop-group" style={{ borderLeftColor: '#ff8c00' }}>
        <div className="prop-title" style={{ color: '#ff8c00' }}>
          🔧 {t('liquid.rack_detail_editor')}: {rack.meta?.name ?? uid}
        </div>
        <div className="prop-hint">
          {/* The same unit as the boxes below it: a panel reading `W: 0.600 m`
              two rows above a width box labelled (mm) reading 500 is one editor
              saying two different things. */}
          📦 {t('label.width_w')}: {dim(width)} {unitLabel} × {t('label.depth_d')}: {dim(depth)}{' '}
          {unitLabel} × H: {dim(height)} {unitLabel}
          <br />
          U-slots: {slots} ({unitMm}mm/U)
          <br />
          {t('inspector.equipment_settings')}: {serverList.length}
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#4a9eff' }}>
        <div className="prop-title" style={{ color: '#4a9eff' }}>
          {t('rack.display_options')}
        </div>
        <div className="prop-row">
          <label>{t('rack.show_context')}</label>
          <input
            id="rd-show-context"
            type="checkbox"
            checked={detail.showContext}
            onChange={(e) => setShowContext(e.target.checked)}
          />
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#00b4dc' }}>
        <div className="prop-title" style={{ color: '#00b4dc' }}>
          {t('rack.cooling_type_title')}
        </div>
        <div className="prop-row">
          <label>{t('rack.cooling_type')}</label>
          <select
            id="rd-cooling-type"
            value={String(cooling.type ?? 'air')}
            onChange={(e) => setCoolingField(uid, 'type', e.target.value)}
          >
            <option value="air">{t('rack.cooling_air')}</option>
            <option value="liquid">{t('rack.cooling_liquid')}</option>
          </select>
        </div>
      </div>

      {isLiquidCooled(rack) && <ManifoldSection uid={uid} rack={rack} />}

      <div className="prop-group" style={{ borderLeftColor: '#2a7a5a' }}>
        <div className="prop-title" style={{ color: '#2a7a5a' }}>
          {t('rack.inner_width_title')}
        </div>
        <div className="prop-row">
          <label>
            {t('rack.inner_width_label')} ({unitLabel})
          </label>
          <NumberField
            id="rd-innerwidth-input"
            value={toDisplay(width * ratio, unit)}
            width={70}
            step={lengthStep}
            min={toDisplay(0.01, unit)}
            onCommit={(v) => setInnerWidthMeters(uid, fromDisplay(v, unit), width)}
          />
          <input
            id="rd-innerwidth-range"
            type="range"
            min={20}
            max={95}
            step={1}
            value={Math.round(ratio * 100)}
            onChange={(e) => live(() => setInnerWidthRatio(uid, Number(e.target.value) / 100, true))}
            onPointerUp={endDrag}
            onBlur={endDrag}
            style={{ flex: 1 }}
          />
          <span id="rd-innerwidth-pct" className="mono-pct">
            {Math.round(ratio * 100)}%
          </span>
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#5a6a8a' }}>
        <div className="prop-title" style={{ color: '#5a6a8a' }}>
          {t('rack.slot_depth_title')}
        </div>
        {/* The row sliders own the blank-baffle / U-slot STRUCTURE depth only.
            Servers carry absolute setbacks now, so a drag here no longer moves
            or rescales them — say so where the drag happens (spec §6). */}
        <div className="prop-hint" id="rd-slot-depth-decoupled">
          {t('rack.slot_depth_decoupled_hint')}
        </div>
        <div className="prop-row">
          <label style={{ color: '#4af' }}>
            {t('rack.inlet_offset')} ({unitLabel})
          </label>
          <NumberField
            id="rd-inlet-input"
            value={toDisplay(inlet, unit)}
            step={lengthStep}
            min={0}
            onCommit={(v) => setDoorOffset(uid, 'inlet', fromDisplay(v, unit), false)}
          />
        </div>
        <div className="prop-row">
          <label style={{ color: '#f64' }}>
            {t('rack.outlet_offset')} ({unitLabel})
          </label>
          <NumberField
            id="rd-outlet-input"
            value={toDisplay(outlet, unit)}
            step={lengthStep}
            min={0}
            onCommit={(v) => setDoorOffset(uid, 'outlet', fromDisplay(v, unit), false)}
          />
        </div>
        <div className="prop-row">
          <label style={{ color: '#4af', fontSize: '0.7rem' }}>inlet %</label>
          <input
            id="rd-inlet-range"
            type="range"
            min={0}
            max={50}
            step={1}
            value={pct(inlet)}
            onChange={(e) =>
              live(() => setDoorOffset(uid, 'inlet', (Number(e.target.value) / 100) * depth, true))
            }
            onPointerUp={endDrag}
            onBlur={endDrag}
            style={{ flex: 1 }}
          />
          <span id="rd-inlet-pct" className="mono-pct">
            {pct(inlet)}%
          </span>
        </div>
        <div className="prop-row">
          <label style={{ color: '#f64', fontSize: '0.7rem' }}>outlet %</label>
          <input
            id="rd-outlet-range"
            type="range"
            min={0}
            max={50}
            step={1}
            value={pct(outlet)}
            onChange={(e) =>
              live(() => setDoorOffset(uid, 'outlet', (Number(e.target.value) / 100) * depth, true))
            }
            onPointerUp={endDrag}
            onBlur={endDrag}
            style={{ flex: 1 }}
          />
          <span id="rd-outlet-pct" className="mono-pct">
            {pct(outlet)}%
          </span>
        </div>
        <div className="prop-row" style={{ borderTop: '1px solid var(--border)', paddingTop: 3 }}>
          <label style={{ fontSize: '0.7rem' }}>{t('rack.both_offsets')}</label>
          <input
            id="rd-both-range"
            type="range"
            min={0}
            max={50}
            step={1}
            value={Math.max(pct(inlet), pct(outlet))}
            onChange={(e) =>
              live(() => setDoorOffset(uid, 'both', (Number(e.target.value) / 100) * depth, true))
            }
            onPointerUp={endDrag}
            onBlur={endDrag}
            style={{ flex: 1 }}
          />
          <span id="rd-both-pct" className="mono-pct">
            {Math.max(pct(inlet), pct(outlet))}%
          </span>
        </div>
        <div className="prop-hint" id="rd-effective-depth">
          {t('equipment.effective_depth')}: {effectiveDepth(depth, inlet, outlet).toFixed(3)} m
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#8a6a2a' }}>
        <div className="prop-title" style={{ color: '#8a6a2a' }}>
          {t('label.blanking_panel_type')}
        </div>
        <select
          id="rd-blanking-type"
          value={String(cooling.blankingPanelType ?? 'typicalSmallGap')}
          onChange={(e) => setCoolingField(uid, 'blankingPanelType', e.target.value)}
          style={{ width: '100%' }}
        >
          <option value="fullBlock">{t('option.blanking_full_block')}</option>
          <option value="typicalSmallGap">{t('option.blanking_small_gap')}</option>
        </select>
        <div className="prop-hint">
          {String(cooling.blankingPanelType ?? 'typicalSmallGap') === 'typicalSmallGap'
            ? t('hint.blanking_small_gap')
            : t('hint.blanking_full_block')}
        </div>
      </div>

      <div className="prop-group" style={{ borderLeftColor: '#c06030' }}>
        <div className="prop-title" style={{ color: '#c06030' }}>
          {t('rack.perforated_door')}
        </div>
        {(['inlet', 'outlet'] as const).map((side) => {
          const door = side === 'inlet' ? inletDoor : outletDoor
          const perf = (door?.perforation ?? {}) as Record<string, unknown>
          return (
            <div key={side}>
              <div className="prop-row">
                <label style={{ color: side === 'inlet' ? '#4af' : '#f64' }}>
                  {side === 'inlet' ? t('rack.inlet_door') : t('rack.outlet_door')}
                </label>
                <input
                  id={`rd-door-${side}`}
                  type="checkbox"
                  checked={!!door}
                  onChange={(e) => setDoorEnabled(uid, side, e.target.checked)}
                />
                {door ? (
                  <>
                    <label style={{ fontSize: '0.7rem' }}>{t('rack.render_door')}</label>
                    <input
                      id={`rd-door-${side}-render`}
                      type="checkbox"
                      checked={door.renderDoor !== false}
                      onChange={(e) => setDoorRender(uid, side, e.target.checked)}
                    />
                  </>
                ) : (
                  <span className="prop-hint">{t('rack.door_not_enabled')}</span>
                )}
              </div>
              {/* The same editor the floor, baffle and patch panels use, rather
                  than two of its four fields hand-copied: a door had no hole
                  SHAPE control and no K readout, so every rack door in this app
                  was a circle-hole door — and `shape` is exported into the
                  .cfdrun, where it changes the door's pressure loss.
                  `renderPerforatedInspector` is what the legacy calls here too
                  (STL_generate.html:9214, :9228). */}
              {door && (
                <PerforatedEditor
                  perf={perf as Perforation}
                  idPrefix={`rd-door-${side}`}
                  onChange={(field, value) => setDoorPerforation(uid, side, field, value)}
                />
              )}
            </div>
          )
        })}
      </div>

      {serverList.length > 0 && (
        <div className="prop-group" style={{ borderLeftColor: '#5a6a8a' }}>
          <div className="prop-title" style={{ color: '#5a6a8a' }}>
            {t('liquid.server_depth')}
          </div>
          {/* ABSOLUTE per-server setbacks (spec §6): metres from the rack inlet
              face and from the rack outlet face, the two stored fields — the
              rack's row insets take no part, and the depth readout below is the
              derived one. A pair that breaks §3.1 or intersects another server
              in the same U's is refused with a message, not clamped. */}
          {serverList.map((srv) => {
            const sUid = String(srv.uid ?? '')
            const inOff = Number(srv.inletOffset ?? 0)
            const outOff = Number(srv.outletOffset ?? 0)
            const meta = (srv.meta ?? {}) as { name?: string }
            const maxSum = rackState().SERVER_DEPTH_MAX_FRACTION * depth
            const r3 = (x: number) => Number(x.toFixed(3))
            function writeServer(nextIn: number, nextOut: number, isLive: boolean) {
              const result = setServerDepthAbsolute(uid, sUid, nextIn, nextOut, isLive)
              const msg = depthErrorText(result)
              setServerError((prev) =>
                msg ? { uid: sUid, msg } : prev?.uid === sUid ? null : prev,
              )
              return result.ok
            }
            return (
              <div key={sUid}>
                <div className="prop-hint">{meta.name ?? sUid}</div>
                {(['inlet', 'outlet'] as const).map((side) => {
                  const v = side === 'inlet' ? inOff : outOff
                  const other = side === 'inlet' ? outOff : inOff
                  const max = Math.max(0, maxSum - other)
                  return (
                    <div className="prop-row" key={side}>
                      <label
                        style={{ fontSize: '0.7rem', color: side === 'inlet' ? '#4af' : '#f64' }}
                        htmlFor={`rd-server-${sUid}-${side}`}
                      >
                        {side === 'inlet' ? t('equipment.extra_inlet') : t('equipment.extra_outlet')}
                      </label>
                      <NumberField
                        id={`rd-server-${sUid}-${side}`}
                        value={v}
                        width={64}
                        step={0.01}
                        min={0}
                        max={r3(max)}
                        decimals={3}
                        onCommit={(nv) =>
                          writeServer(side === 'inlet' ? nv : inOff, side === 'outlet' ? nv : outOff, false)
                        }
                      />
                      <input
                        id={`rd-server-${sUid}-${side}-range`}
                        type="range"
                        min={0}
                        max={r3(max)}
                        step={0.001}
                        value={r3(v)}
                        onChange={(e) => {
                          const nv = Number(e.target.value)
                          live(() =>
                            writeServer(
                              side === 'inlet' ? nv : inOff,
                              side === 'outlet' ? nv : outOff,
                              true,
                            ),
                          )
                        }}
                        onPointerUp={endDrag}
                        onBlur={endDrag}
                        style={{ flex: 1 }}
                      />
                      <span className="mono-pct" id={`rd-server-${sUid}-${side}-val`}>
                        {v.toFixed(3)} m
                      </span>
                    </div>
                  )
                })}
                <div className="prop-hint">
                  {t('label.server_depth')}: {Math.max(0, effectiveDepth(depth, inOff, outOff)).toFixed(3)} m
                </div>
                {serverError?.uid === sUid && (
                  <div className="error" id={`rd-server-${sUid}-error`}>
                    {serverError.msg}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <div className="prop-group" style={{ borderLeftColor: '#5a3a3a' }}>
        <button className="btn danger frozen-ok" id="rd-exit-btn" onClick={() => exitRackDetail()}>
          ↩ {t('btn.exit_detail_editor')}
        </button>
      </div>
    </div>
  )
}

/**
 * The manifold section, shown only while the rack is liquid-cooled.
 *
 * Ports the `_lmHtml` block (STL_generate.html:9057-9086), which sits between
 * the cooling-type selector and the mounting-width slider. Millimetres in the
 * boxes, metres in the project — see `rackManifold`.
 */
function ManifoldSection({ uid, rack }: { uid: string; rack: Construction }) {
  const { t } = useTranslation()
  const lm = manifoldView(rack)
  return (
    <div className="prop-group" id="rd-manifold" style={{ borderLeftColor: '#00b4dc' }}>
      <div className="prop-title" style={{ color: '#00b4dc' }}>
        {t('liquid.manifold_layout_title')}
      </div>
      <div className="prop-row">
        <label htmlFor="rd-manifold-layout">{t('liquid.manifold_layout')}</label>
        <select
          id="rd-manifold-layout"
          value={lm.layout}
          onChange={(e) => setManifoldLayout(uid, e.target.value)}
        >
          <option value="both-left">{t('liquid.layout_both_left')}</option>
          <option value="both-right">{t('liquid.layout_both_right')}</option>
          <option value="split">{t('liquid.layout_split')}</option>
        </select>
      </div>
      <div className="prop-row">
        <label htmlFor="rd-manifold-face">{t('liquid.manifold_mount_face')}</label>
        <select
          id="rd-manifold-face"
          value={lm.mountFace}
          onChange={(e) => setManifoldMountFace(uid, e.target.value)}
        >
          <option value="inlet">{t('liquid.face_inlet')}</option>
          <option value="outlet">{t('liquid.face_outlet')}</option>
        </select>
      </div>
      <div className="prop-row">
        <label htmlFor="rd-manifold-diameter">{t('liquid.manifold_diameter')}</label>
        <NumberField
          id="rd-manifold-diameter"
          value={lm.diameterMm}
          decimals={0}
          step={1}
          min={10}
          max={50}
          width={60}
          onCommit={(v) => setManifoldParam(uid, 'diameter', v)}
        />
      </div>
      <div className="prop-row">
        <label htmlFor="rd-manifold-offset">{t('liquid.manifold_offset')}</label>
        <NumberField
          id="rd-manifold-offset"
          value={lm.offsetMm}
          decimals={0}
          step={5}
          min={30}
          max={200}
          width={60}
          onCommit={(v) => setManifoldParam(uid, 'offset', v)}
        />
      </div>
    </div>
  )
}
