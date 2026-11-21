import type { Construction } from '../schema/cfdproj'
import {
  serverEquipmentExportName,
  serverSlotRange,
} from '../geometry/serverExportNames'
import { setServerFaceColor } from '../geometry/serverPatches'
import { setServerDepthAbsolute, type ServerDepthResult } from '../geometry/rackDetail'
import { useEffect, useRef, useState } from 'react'
import * as store from '../projectStore'
import { updateRackServerCfd } from '../geometry/rackServers'
import { rackState } from '../legacy/cfdprojModules'
import { opacityToPercent } from '../geometry/modelOpacity'
import {
  commitServerOpacity,
  previewServerOpacity,
  serverOpacityOf,
  type ServerRecord,
} from '../geometry/serverOpacity'
import { rackFaceLabel } from '../geometry/rackFaceLabels'
import { useTranslation } from '../useTranslation'
import { ColdPlateSection } from './ColdPlateSection'
import { NumberField } from './NumberField'
import { OpacitySlider } from './OpacitySlider'
import { RackStackedServerSection } from './RackStackedServerSection'
import { KeyframeBadge } from '../transient/KeyframeBadge'

/**
 * A rack server's inspector: what it is, and how it heats the room.
 *
 * The CFD section is the point of it. A fan-server is a flow boundary — it
 * takes air in one face and pushes it out the other — and its physics is stated
 * as any TWO of power, flow rate and temperature rise; the third follows from
 * the other two. That is why the mode dropdown HIDES a field rather than
 * greying it: showing all three invites a user to over-specify and wonder which
 * one the solver believed.
 *
 * Everything else in a rack (a switch, a PDU, a blanking plate) is not a flow
 * boundary at all, so it gets the per-FACE form instead: each face is powered
 * or not, and a powered face is either a fixed temperature or a fixed heat
 * load. Ported from `renderRackEquipmentInspector` / `renderEquipmentCfdInspector`
 * (STL_generate.html:10037, 10295).
 *
 * A LIQUID server gets a third form on top: its cold plates
 * (`ColdPlateSection`). The legacy shows that INSTEAD of the CFD group, but only
 * while the app is in liquid mode (STL_generate.html:10052) — a mode that lives
 * in App state here and is published to the legacy globals from an effect, so
 * reading it back during a render answers with the PREVIOUS mode. Keying on the
 * server's own type instead costs one extra group on screen and never hides the
 * air-side boundary of a liquid server, which still exports as a `nonFan` patch
 * set (js/cfdrun_export.js:751) and has to stay editable.
 *
 * Values live in `constructions.servers[uid].cooling` — the V4 home, not the
 * old `cfd.servers[]` array.
 */

export interface EquipmentInspectorProps {
  model: Construction
  equipmentId: string
}

type Cooling = Record<string, unknown>

function serversOf(model: Construction): Record<string, Record<string, unknown>> {
  return ((model.constructions as { servers?: Record<string, Record<string, unknown>> })?.servers ??
    {}) as Record<string, Record<string, unknown>>
}

export function EquipmentInspector({ model, equipmentId }: EquipmentInspectorProps) {
  const { t } = useTranslation()
  const server = serversOf(model)[equipmentId]
  if (!server) return null

  const meta = (server.meta ?? {}) as { name?: string }
  const cooling = (server.cooling ?? {}) as Cooling
  // V4 stores the discriminator as `type`; `kind` is the v3 spelling that
  // `_rackServerView` maps onto it (js/rack_state.js:366). Reading only `kind`
  // left every V4 server typeless, so the type row showed a dash and a
  // never-edited fanServer fell through to the per-face form.
  const kind = String(
    server.type ?? server.kind ?? (server as { equipmentKind?: string }).equipmentKind ?? '',
  )
  const isFan = kind === 'fanServer' || cooling.serverBCMode !== undefined

  // The U-slots this server covers, so the panel can host the stacked-servers
  // add path (WP-R). Reaching it through the slot inspector alone missed the
  // real flow: a click on an occupied slot lands on the SERVER face, so the
  // equipment panel — not the slot panel — is what opens, and without this
  // section "add another server at the same U" was unreachable by normal use.
  // Plain derived data, not a hook: computed before the JSX, after the early
  // return, so the hook order is untouched. Missing or malformed slot data
  // renders nothing rather than guessing a range.
  const slotGeometry = (server.geometry ?? {}) as {
    slotPosition?: unknown
    slotOccupation?: unknown
  }
  const slotPosition = Number(slotGeometry.slotPosition)
  const slotOccupation = Number(slotGeometry.slotOccupation)
  const serverSlotIndices =
    Number.isInteger(slotPosition) &&
    slotPosition >= 0 &&
    Number.isInteger(slotOccupation) &&
    slotOccupation >= 1
      ? Array.from({ length: slotOccupation }, (_, i) => slotPosition + i)
      : null

  // Delegates so the panel and `window.updateRackServerCfd` (the automation
  // contract, which is how /dc-builder powers 200 racks) write through ONE
  // definition of what a server's BC seed is.
  function set(field: string, value: unknown) {
    updateRackServerCfd(model.uid, equipmentId, field, value)
  }

  function rename(name: string) {
    store.edit('renameRackEquipment', `${equipmentId} -> ${name}`, (draft) => {
      const target = store.findConstruction(draft, model.uid)?.model
      const srv = target ? serversOf(target)[equipmentId] : null
      if (!srv) return
      srv.meta = { ...((srv.meta ?? {}) as object), name }
    })
  }

  const faces = (server.faces ?? {}) as Record<string, { color?: string }>
  const faceColor = (faceId: 'inlet' | 'outlet') =>
    String(faces[faceId]?.color ?? (faceId === 'inlet' ? '#d14b4b' : '#9f3636'))

  const bcMode = String(cooling.serverBCMode ?? 'powerAndFlow')
  const powerMode = String(cooling.powerMode ?? 'powered')
  const showPower = bcMode !== 'tempRiseAndFlow'
  const showFlow = bcMode !== 'powerAndTempRise'
  const showRise = bcMode !== 'powerAndFlow'

  return (
    <div id="equipment-inspector">
      <div className="prop-group">
        <div className="prop-title">{t('inspector.equipment_settings')}</div>
        <div className="prop-row">
          <label>{t('label.equipment_name')}</label>
          <input
            id="eq-name"
            type="text"
            defaultValue={meta.name ?? equipmentId}
            key={equipmentId}
            onBlur={(e) => e.target.value !== meta.name && rename(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          />
        </div>
        <div className="prop-row">
          <label>{t('label.equipment_type')}</label>
          <span className="value">{kind || '—'}</span>
        </div>
        {/* Which U-slots the server occupies. The panel is reached from the
            tree as often as from a slot, and from there nothing else on screen
            says where in the rack this server sits. */}
        <div className="prop-row">
          <label>{t('label.equipment_range')}</label>
          <span className="value" id="eq-range">
            {serverSlotRange(model, equipmentId) || '—'}
          </span>
        </div>
        <div className="prop-row">
          <label>{t('inspector.uid')}</label>
          <span className="value">{equipmentId}</span>
        </div>
        <div className="prop-hint">{t('hint.equipment_linkage')}</div>
      </div>

      <div className="prop-group">
        <div className="prop-title">Naming &amp; Color</div>
        <div className="prop-row">
          <label htmlFor="eq-front-color">{t('label.inlet_color')}</label>
          <input
            id="eq-front-color"
            type="color"
            value={faceColor('inlet')}
            onChange={(e) => setServerFaceColor(model.uid, equipmentId, 'inlet', e.target.value)}
          />
        </div>
        <div className="prop-row">
          <label htmlFor="eq-back-color">{t('label.outlet_color')}</label>
          <input
            id="eq-back-color"
            type="color"
            value={faceColor('outlet')}
            onChange={(e) => setServerFaceColor(model.uid, equipmentId, 'outlet', e.target.value)}
          />
        </div>
        {/* Read-only: the solid name is DERIVED from the rack name, this
            server's name and its slot range, and it IS the OpenFOAM patch name.
            A name typed here would be recomputed on export, moving a boundary
            condition onto a surface nobody chose. */}
        <div className="prop-row">
          <label htmlFor="eq-stl-region">STL Region</label>
          <input
            id="eq-stl-region"
            type="text"
            value={serverEquipmentExportName(model, equipmentId)}
            disabled
          />
        </div>
      </div>

      <ServerDepthControls model={model} equipmentId={equipmentId} />

      {/* The servers sharing this one's U range, and the form that adds
          another. The section carries its own title group
          (rack.stacked_servers_title) and hides itself when the range holds no
          server, so it is embedded as-is. */}
      {serverSlotIndices && (
        <RackStackedServerSection model={model} indices={serverSlotIndices} />
      )}

      {/* The per-server opacity knob (spec §5.1): writes THIS server's
          meta.display.opacity — the only storage there is. The rack panel's
          batch slider writes the same field on every server at once; neither
          slider drags the rack body any more. */}
      <div className="prop-group">
        <div className="prop-title">{t('label.server_opacity')}</div>
        <div className="prop-row">
          <label htmlFor="eq-opacity-slider">{t('label.server_opacity')}</label>
          <OpacitySlider
            id="eq-opacity-slider"
            badgeId="eq-opacity-badge"
            committed={opacityToPercent(serverOpacityOf(server as ServerRecord))}
            onPreview={(p) => previewServerOpacity(model.uid, equipmentId, p)}
            onCommit={(p) => commitServerOpacity(model.uid, equipmentId, p)}
          />
        </div>
        <div className="prop-hint">{t('hint.server_opacity_desc')}</div>
      </div>

      {kind === 'liquidServer' && <ColdPlateSection model={model} serverId={equipmentId} />}

      <div className="prop-group" style={{ borderLeftColor: '#16825d' }}>
        <div className="prop-title" style={{ color: '#16825d' }}>
          {t('inspector.cfd_physics')}
        </div>

        {isFan && (
          <div className="prop-row">
            <label htmlFor="eq-fan-power-mode">{t('label.device_mode')}</label>
            <select
              id="eq-fan-power-mode"
              value={powerMode}
              onChange={(e) => set('powerMode', e.target.value)}
            >
              <option value="powered">{t('label.powered')}</option>
              <option value="unpowered">{t('label.unpowered')}</option>
            </select>
          </div>
        )}
        {isFan && powerMode === 'unpowered' ? (
          <div className="prop-hint">{t('hint.unpowered_fan_desc')}</div>
        ) : isFan ? (
          <>
            <div className="prop-row">
              <label>{t('label.server_bc_mode')}</label>
              <select
                id="eq-bc-mode"
                value={bcMode}
                onChange={(e) => set('serverBCMode', e.target.value)}
              >
                <option value="powerAndFlow">{t('option.power_and_flow')}</option>
                <option value="tempRiseAndFlow">{t('option.temp_rise_and_flow')}</option>
                <option value="powerAndTempRise">{t('option.power_and_temp_rise')}</option>
              </select>
            </div>
            {showFlow && (
              <div className="prop-row">
                <label>{t('label.volume_flow_rate')}</label>
                <NumberField
                  id="eq-flow"
                  value={Number(cooling.volumeFlowRate ?? 0.5)}
                  step={0.01}
                  min={0}
                  onCommit={(v) => set('volumeFlowRate', v)}
                />
              </div>
            )}
            {showPower && (
              <div className="prop-row kf-field">
                <label>{t('label.power_w')}<KeyframeBadge modelUid={model.uid} param="server.power" target={equipmentId} /></label>
                <NumberField
                  id="eq-power"
                  value={Number(cooling.power ?? 1000)}
                  step={50}
                  min={0}
                  onCommit={(v) => set('power', v)}
                />
              </div>
            )}
            {showRise && (
              <div className="prop-row">
                <label>{t('label.temperature_rise')}</label>
                <NumberField
                  id="eq-temp-rise"
                  value={Number(cooling.temperatureRise ?? 10)}
                  step={0.5}
                  onCommit={(v) => set('temperatureRise', v)}
                />
              </div>
            )}
            <div className="prop-hint">{t('hint.fan_server_dt')}</div>
          </>
        ) : (
          <NonFanPhysics cooling={cooling} set={set} />
        )}
      </div>
    </div>
  )
}

/**
 * Where the server sits along the rack's depth — ABSOLUTE (spec §6).
 *
 * The stored numbers are the server's own `inletOffset` / `outletOffset`:
 * metres back from the rack inlet face and from the rack outlet face. Nothing
 * is derived from the rack's row insets any more, so the rack-detail row
 * sliders cannot move a server, and the panel edits the two stored fields
 * directly. The server's DEPTH is the derived one now: a read-only
 * `D − inletOffset − outletOffset`.
 *
 * Both fields have a number box and a quick-set slider. The feasible set is
 * §3.1: each offset ≥ 0 and the pair ≤ 90% of D — the same bound the shared
 * `setServerDepthAbsolute` enforces, so no hidden clamp fights a slider, and a
 * pair that would intersect another server in the same U's is REFUSED with the
 * conflict message rather than quietly moved.
 */
function ServerDepthControls({ model, equipmentId }: { model: Construction; equipmentId: string }) {
  const { t } = useTranslation()
  const rack = model as unknown as { geometry?: { size?: { z?: number } } }
  const D = Number(rack.geometry?.size?.z ?? 0)
  const srv = serversOf(model)[equipmentId] as Record<string, unknown> | undefined
  const inletOff = Number(srv?.inletOffset ?? 0)
  const outletOff = Number(srv?.outletOffset ?? 0)
  const depth = D - inletOff - outletOff
  const maxSum = rackState().SERVER_DEPTH_MAX_FRACTION * D
  const maxInlet = Math.max(0, maxSum - outletOff)
  const maxOutlet = Math.max(0, maxSum - inletOff)
  const [writeError, setWriteError] = useState<string | null>(null)

  function errorText(result: ServerDepthResult | null): string | null {
    if (!result || result.ok) return null
    return result.error === 'overlap'
      ? t('error.server_depth_overlap')
      : result.error === 'range'
        ? t('error.server_depth_range')
        : null
  }

  // The slider drag in progress, or null. Everything the release needs is in
  // here, not in render closures: the commit can run after re-renders, from a
  // native `change` listener, or from an unmount.
  const drag = useRef<ServerDrag | null>(null)
  const endDrag = useRef(() => {
    const d = drag.current
    drag.current = null
    if (d) setWriteError(errorText(commitServerDrag(d)))
  }).current

  // A drag still open when the panel goes away (or switches to another
  // server) is committed, not left as dirty live values outside undo.
  useEffect(() => endDrag, [endDrag, model.uid, equipmentId])

  // A range input's native `change` is its release — pointer or keyboard. React
  // folds `change` into onChange with `input`, so it is listened to natively,
  // here, where it arrives before React's root listener sees it.
  const groupRef = useRef<HTMLDivElement>(null)
  const shown = D > 0
  useEffect(() => {
    const el = groupRef.current
    if (!el) return
    const onNativeChange = (e: Event) => {
      if ((e.target as HTMLInputElement | null)?.type === 'range') endDrag()
    }
    el.addEventListener('change', onNativeChange)
    return () => el.removeEventListener('change', onNativeChange)
  }, [shown, endDrag])

  function write(newInlet: number, newOutlet: number): boolean {
    const result = setServerDepthAbsolute(model.uid, equipmentId, newInlet, newOutlet, false)
    setWriteError(errorText(result))
    // `false` tells the NumberField the edit was refused, and it puts the
    // model's own value back — the box must not show a number the document
    // does not hold.
    return result.ok
  }

  /** The drag, begun on its first frame from the COMMITTED setbacks. */
  function begin(): ServerDrag {
    if (!drag.current) {
      const start = committedOffsets(model.uid, equipmentId) ?? {
        inletOffset: inletOff,
        outletOffset: outletOff,
      }
      drag.current = {
        uid: model.uid,
        equipmentId,
        maxSum,
        startInlet: start.inletOffset,
        startOutlet: start.outletOffset,
        lastInlet: start.inletOffset,
        lastOutlet: start.outletOffset,
        moved: false,
      }
    }
    return drag.current
  }

  // A slider value within half a step of what is already shown is not a move:
  // it is the browser's trailing `change` re-reporting the released value, or
  // the thumb back on its starting notch.
  const onInletSlider = (v: number) => {
    if (!Number.isFinite(v)) return
    if (!drag.current && Math.abs(v - r3(inletOff)) < HALF_STEP) return
    const d = begin()
    // The OTHER offset is anchored on where the drag STARTED: the two fields
    // are independent absolutes, and re-reading the live one every frame would
    // let a refused (overlap) frame ratchet through the next accepted one.
    const next =
      Math.abs(v - r3(d.startInlet)) < HALF_STEP
        ? d.startInlet
        : Math.min(Math.max(0, v), Math.max(0, d.maxSum - d.startOutlet))
    setWriteError(errorText(liveServerFrame(d, next, d.startOutlet)))
  }

  const onOutletSlider = (v: number) => {
    if (!Number.isFinite(v)) return
    if (!drag.current && Math.abs(v - r3(outletOff)) < HALF_STEP) return
    const d = begin()
    const next =
      Math.abs(v - r3(d.startOutlet)) < HALF_STEP
        ? d.startOutlet
        : Math.min(Math.max(0, v), Math.max(0, d.maxSum - d.startInlet))
    setWriteError(errorText(liveServerFrame(d, d.startInlet, next)))
  }

  const release = {
    onPointerUp: endDrag,
    onPointerCancel: endDrag,
    onKeyUp: endDrag,
    onBlur: endDrag,
  }

  if (!shown) return null
  return (
    <div className="prop-group" ref={groupRef} style={{ borderLeftColor: '#5a6a8a' }}>
      <div className="prop-title" style={{ color: '#5a6a8a' }}>{t('label.server_depth_group')}</div>
      <div className="prop-row">
        <label htmlFor="eq-inlet-offset" style={{ color: '#4af' }}>
          {t('equipment.extra_inlet')}
        </label>
        <NumberField
          id="eq-inlet-offset"
          value={inletOff}
          step={0.01}
          min={0}
          max={r3(maxInlet)}
          decimals={3}
          onCommit={(v) => write(v, outletOff)}
        />
      </div>
      <div className="prop-row">
        {/* Value and range rounded to the 1 mm step: an unrounded controlled
            value (0.05299999999999999 against the DOM's 0.053) made React's
            value tracker read the trailing `change` as a new value. */}
        <input
          id="eq-inlet-offset-slider"
          type="range"
          min={0}
          max={r3(maxInlet)}
          step={0.001}
          value={r3(inletOff)}
          onChange={(e) => onInletSlider(Number(e.target.value))}
          {...release}
          style={{ flex: 1 }}
        />
        <span className="mono-pct" id="eq-inlet-offset-val">{inletOff.toFixed(3)} m</span>
      </div>
      <div className="prop-row">
        <label htmlFor="eq-outlet-offset" style={{ color: '#f64' }}>
          {t('equipment.extra_outlet')}
        </label>
        <NumberField
          id="eq-outlet-offset"
          value={outletOff}
          step={0.01}
          min={0}
          max={r3(maxOutlet)}
          decimals={3}
          onCommit={(v) => write(inletOff, v)}
        />
      </div>
      <div className="prop-row">
        <input
          id="eq-outlet-offset-slider"
          type="range"
          min={0}
          max={r3(maxOutlet)}
          step={0.001}
          value={r3(outletOff)}
          onChange={(e) => onOutletSlider(Number(e.target.value))}
          {...release}
          style={{ flex: 1 }}
        />
        <span className="mono-pct" id="eq-outlet-offset-val">{outletOff.toFixed(3)} m</span>
      </div>
      <div className="prop-row">
        {/* DERIVED, read-only: since the absolute fields there is no stored
            depth to edit — it is what the two setbacks leave over. */}
        <label>{t('label.server_depth')}</label>
        <span className="value mono-pct" id="eq-depth-val">
          {Math.max(0, depth).toFixed(3)} m
        </span>
      </div>
      <div className="prop-hint" id="eq-depth-hint">
        {t('hint.server_depth_desc', { depth: D.toFixed(3), inlet: inletOff.toFixed(3) })}
      </div>
      {writeError && (
        <div className="error" id="eq-depth-error">
          {writeError}
        </div>
      )}
    </div>
  )
}

/** Half the sliders' 1 mm step. */
const HALF_STEP = 5e-4

/** Round to the sliders' 1 mm step. */
const r3 = (x: number) => Number(x.toFixed(3))

/** One slider drag of a server's placement, from first frame to release. */
interface ServerDrag {
  uid: string
  equipmentId: string
  /** The §3.1 budget: inletOffset + outletOffset may not eat more. */
  maxSum: number
  /** The committed setbacks the drag began from — what undo must restore. */
  startInlet: number
  startOutlet: number
  lastInlet: number
  lastOutlet: number
  /** Whether any frame reached the live project. */
  moved: boolean
}

/** The server's setbacks as the store holds them, or null when it is gone. */
function committedOffsets(
  uid: string,
  equipmentId: string,
): { inletOffset: number; outletOffset: number } | null {
  const project = store.getProject()
  const model = project ? store.findConstruction(project, uid)?.model : null
  const server = model ? serversOf(model)[equipmentId] : null
  if (!server) return null
  return {
    inletOffset: Number(server.inletOffset ?? 0),
    outletOffset: Number(server.outletOffset ?? 0),
  }
}

/**
 * One frame: live, no undo entry; skipped when nothing moved. A frame the
 * shared writer refuses (an overlap mid-drag) writes nothing and the server
 * stays at the last accepted frame — the refusal is the answer, not a clamp.
 */
function liveServerFrame(
  d: ServerDrag,
  inletOffset: number,
  outletOffset: number,
): ServerDepthResult | null {
  if (
    Math.abs(inletOffset - d.lastInlet) < 1e-12 &&
    Math.abs(outletOffset - d.lastOutlet) < 1e-12
  ) {
    return null
  }
  d.moved = true
  const result = setServerDepthAbsolute(d.uid, d.equipmentId, inletOffset, outletOffset, true)
  if (!result.ok) return result
  d.lastInlet = inletOffset
  d.lastOutlet = outletOffset
  return result
}

/**
 * The release: ONE undo entry that restores where the drag began.
 *
 * `setServerDepthAbsolute(..., dragStart)` puts the starting setbacks back on
 * the live project before it edits, so the snapshot on the undo stack is the
 * start of the gesture. A drag that came back to where it began commits
 * nothing; its frames still wrote the live project, so the exact start is put
 * back. A release the writer refuses leaves the server at its drag start and
 * pushes no entry at all.
 */
function commitServerDrag(d: ServerDrag): ServerDepthResult | null {
  if (!d.moved) return null
  const unchanged =
    Math.abs(d.lastInlet - d.startInlet) < 1e-9 &&
    Math.abs(d.lastOutlet - d.startOutlet) < 1e-9
  if (unchanged) {
    return setServerDepthAbsolute(d.uid, d.equipmentId, d.startInlet, d.startOutlet, true)
  }
  return setServerDepthAbsolute(d.uid, d.equipmentId, d.lastInlet, d.lastOutlet, false, {
    inletOffset: d.startInlet,
    outletOffset: d.startOutlet,
  })
}

/**
 * The per-face form for equipment that does not move air.
 *
 * Each face is powered or not, and a powered face is a fixed temperature OR a
 * fixed power — never both, because they are two ways of saying the same
 * boundary and the runner takes whichever the type says.
 */
function NonFanPhysics({ cooling, set }: { cooling: Cooling; set: (f: string, v: unknown) => void }) {
  const { t } = useTranslation()
  const powerMode = String(cooling.powerMode ?? 'powered')

  return (
    <>
      <div className="prop-row">
        <label>{t('label.device_mode')}</label>
        <select
          id="eq-power-mode"
          value={powerMode}
          onChange={(e) => set('powerMode', e.target.value)}
        >
          <option value="powered">{t('label.powered')}</option>
          <option value="unpowered">{t('label.unpowered')}</option>
        </select>
      </div>
      {powerMode === 'unpowered' ? (
        <div className="prop-hint">{t('hint.unpowered_desc')}</div>
      ) : (
        (['inlet', 'outlet'] as const).map((side) => {
          const mode = String(cooling[`${side}Mode`] ?? 'powered')
          const heatType = String(cooling[`${side}HeatType`] ?? 'fixedTemp')
          const temp = Number(cooling[`${side}Temperature`] ?? cooling.fixedTemperature ?? 35)
          const power = Number(cooling[`${side}Power`] ?? 500)
          return (
            <div key={side}>
              <div style={{ fontWeight: 500, marginTop: 6, color: 'var(--accent3)' }}>
                ◆ {rackFaceLabel(side)}
              </div>
              <div className="prop-row">
                <label>{t('label.face_status')}</label>
                <select
                  id={`eq-${side}-mode`}
                  value={mode}
                  onChange={(e) => set(`${side}Mode`, e.target.value)}
                >
                  <option value="powered">{t('label.powered')}</option>
                  <option value="unpowered">{t('label.unpowered')}</option>
                </select>
              </div>
              {mode === 'powered' && (
                <>
                  <div className="prop-row">
                    <label>{t('label.heat_type')}</label>
                    <select
                      id={`eq-${side}-heat-type`}
                      value={heatType}
                      onChange={(e) => set(`${side}HeatType`, e.target.value)}
                    >
                      <option value="fixedTemp">{t('label.fixed_temperature')}</option>
                      <option value="fixedPower">{t('label.fixed_power')}</option>
                    </select>
                  </div>
                  {heatType === 'fixedTemp' ? (
                    <div className="prop-row">
                      <label>{t('label.temperature_c')}</label>
                      <NumberField
                        id={`eq-${side}-temp`}
                        value={temp}
                        step={1}
                        onCommit={(v) => set(`${side}Temperature`, v)}
                      />
                    </div>
                  ) : (
                    <div className="prop-row">
                      <label>{t('label.power_w')}</label>
                      <NumberField
                        id={`eq-${side}-power`}
                        value={power}
                        step={50}
                        min={0}
                        onCommit={(v) => set(`${side}Power`, v)}
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })
      )}
    </>
  )
}
