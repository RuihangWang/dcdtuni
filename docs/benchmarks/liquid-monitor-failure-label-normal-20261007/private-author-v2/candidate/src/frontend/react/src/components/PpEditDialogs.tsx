import { useCallback, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { COLOR_MAPS, SLICE_NORMALS, type ColorMap } from '../postprocess/ppConfig'
import { streamlineFieldOptions } from '../postprocess/ppFieldOptions'
import { fromDisplay, isTemperatureField, subscribeTempUnit, tempUnit, tempUnitLabel, toDisplay } from '../postprocess/ppUnits'
import { useTranslation } from '../useTranslation'
import { isSliceAutoRange } from '../postprocess/ppSliceRange'
import { NumberField } from './NumberField'

/**
 * Re-opening a slice or a streamline set that has already been computed.
 *
 * Double-clicking a row in `pp-slice-list` / `pp-streamline-list` reopens the
 * parameters it was made with, and 应用 re-cuts or re-integrates IN PLACE:
 * same id, same position in the list, same colorbar slot. Without it a set is
 * write-once — the only way to move a cut 20 cm is to delete it and re-enter
 * every number, and the whole reason a user keeps several is to compare them.
 *
 * Ported from `ppEditStreamlineSet` (STL_generate.html:22050) and `ppEditSlice`
 * (`:23655`), ids included, so one selector addresses both apps.
 *
 * ## Floating, not modal
 *
 * No backdrop. The dialog exists so the viewport can be worked WHILE it is
 * open — the slice one drives the preview ring, which is dragged in the scene
 * behind it — and a modal overlay would swallow exactly those pointer events.
 * It is portalled to `document.body` for the reason `MeshQualityDialog`
 * documents: `#left-panel` carries a `backdrop-filter`, which makes it the
 * containing block for `position: fixed` children, and rendered in place the
 * dialog is clipped to the panel column.
 */

type Vec3 = [number, number, number]

export interface SliceEditForm {
  /** Absent on historical forms means manual; only true chooses the data range. */
  autoScale?: boolean
  normalPreset: string
  normal: Vec3
  origin: Vec3
  field: string
  colorMap: ColorMap
  climMin: number
  climMax: number
}

export interface StreamlineEditForm {
  centre: Vec3
  radius: number
  count: number
  tubeRadius: number
  field: string
  colorMap: ColorMap
}

const INPUT_WIDTH = 70

/** The window chrome both dialogs share: a drag bar, a close cross, a frame. */
function FloatingDialog(props: {
  id: string
  titlebarId: string
  closeId: string
  title: string
  accent: string
  onClose: () => void
  children: ReactNode
}) {
  const [offset, setOffset] = useState<{ x: number; y: number }>({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null)

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = drag.current
    if (!d) return
    setOffset({ x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) })
  }, [])
  const onPointerUp = useCallback(() => {
    drag.current = null
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
  }, [onPointerMove])

  return createPortal(
    <div
      id={props.id}
      style={{ position: 'fixed', top: 0, left: 0, width: 0, height: 0, zIndex: 10000, pointerEvents: 'none' }}
    >
      <div
        style={{
          position: 'fixed',
          top: 80 + offset.y,
          right: 60 - offset.x,
          background: 'var(--bg-sidebar,#1e1e2e)',
          border: '1px solid var(--border,#444)',
          borderRadius: 8,
          padding: 16,
          width: 320,
          maxWidth: '90vw',
          maxHeight: '80vh',
          overflowY: 'auto',
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          color: 'var(--text,#ccc)',
          fontSize: '0.8rem',
          pointerEvents: 'auto',
          zIndex: 10001,
        }}
      >
        <div
          id={props.titlebarId}
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y }
            window.addEventListener('pointermove', onPointerMove)
            window.addEventListener('pointerup', onPointerUp)
          }}
          style={{
            fontSize: '0.9rem',
            fontWeight: 'bold',
            marginBottom: 12,
            color: props.accent,
            cursor: 'move',
            userSelect: 'none',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span>{props.title}</span>
          <span
            id={props.closeId}
            onClick={props.onClose}
            style={{ cursor: 'pointer', fontSize: '1.1rem', color: 'var(--text-dim)', padding: '0 4px' }}
          >
            ✕
          </span>
        </div>
        {props.children}
      </div>
    </div>,
    document.body,
  )
}

function Row(props: { children: ReactNode; gap?: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: props.gap ?? 6, marginBottom: 6 }}>
      {props.children}
    </div>
  )
}

function ColorMapSelect(props: { id: string; value: ColorMap; onChange: (v: ColorMap) => void }) {
  return (
    <select
      id={props.id}
      value={props.value}
      onChange={(e) => props.onChange(e.target.value as ColorMap)}
      style={{ flex: 1, minWidth: 0 }}
    >
      {COLOR_MAPS.map((c) => (
        <option key={c.id} value={c.id}>
          {c.label}
        </option>
      ))}
    </select>
  )
}

export interface SliceEditDialogProps {
  id: number
  form: SliceEditForm
  fields: string[]
  busy: boolean
  onChange: (patch: Partial<SliceEditForm>) => void
  onClose: () => void
  onApply: () => void
}

/** 编辑切面 — the same six controls the sidebar has, bound to one existing cut. */
export function PpSliceEditDialog({ id, form, fields, busy, onChange, onClose, onApply }: SliceEditDialogProps) {
  const { t } = useTranslation()
  const displayTempUnit = useSyncExternalStore(subscribeTempUnit, tempUnit)
  const rangeUnit = isTemperatureField(form.field) ? ' (' + tempUnitLabel(displayTempUnit) + ')' : ''
  const custom = form.normalPreset === 'custom'
  const axis = form.normalPreset === 'x' ? 0 : form.normalPreset === 'z' ? 2 : 1

  return (
    <FloatingDialog
      id="pp-slice-edit-overlay"
      titlebarId="dlg-slice-titlebar"
      closeId="dlg-slice-close"
      title={`${t('pp.edit_slice_title')} #${id}`}
      accent="var(--section-header-block,#e5c07b)"
      onClose={onClose}
    >
      <Row>
        <label style={{ width: 60 }}>{t('pp.slice_normal')}</label>
        <select
          id="dlg-slice-normal"
          value={form.normalPreset}
          style={{ flex: 1, minWidth: 0 }}
          onChange={(e) => {
            const preset = SLICE_NORMALS.find((n) => n.id === e.target.value)
            onChange({
              normalPreset: e.target.value,
              normal: preset && preset.id !== 'custom' ? preset.normal : form.normal,
            })
          }}
        >
          {SLICE_NORMALS.map((n) => (
            <option key={n.id} value={n.id}>
              {n.label}
            </option>
          ))}
        </select>
      </Row>
      {custom && (
        <div id="dlg-slice-custom-normal" style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
          {(['x', 'y', 'z'] as const).map((a, i) => (
            <div key={a} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 0 }}>
              <label style={{ width: 24 }}>n{a.toUpperCase()}:</label>
              <NumberField
                id={`dlg-slice-n${a}`}
                width={INPUT_WIDTH}
                value={form.normal[i] as number}
                step={0.1}
                onCommit={(v) => {
                  const normal = [...form.normal] as Vec3
                  normal[i] = v
                  onChange({ normal, normalPreset: 'custom' })
                }}
              />
            </div>
          ))}
        </div>
      )}
      {!custom && (
        <div id="dlg-slice-origin-axis" style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          <label id="dlg-slice-origin-label" style={{ width: 60 }}>
            {form.normalPreset.toUpperCase()} {t('pp.slice_position')}
          </label>
          <NumberField
            id="dlg-slice-origin-single"
            width={INPUT_WIDTH}
            value={form.origin[axis] as number}
            step={0.0001}
            onCommit={(v) => {
              const origin = [...form.origin] as Vec3
              origin[axis] = v
              onChange({ origin })
            }}
          />
        </div>
      )}
      {custom && (
        <div id="dlg-slice-origin-custom" style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
          {(['x', 'y', 'z'] as const).map((a, i) => (
            <div key={a} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 0 }}>
              <label style={{ width: 18 }}>{a.toUpperCase()}:</label>
              <NumberField
                id={`dlg-slice-o${a}`}
                width={INPUT_WIDTH}
                value={form.origin[i] as number}
                step={0.0001}
                onCommit={(v) => {
                  const origin = [...form.origin] as Vec3
                  origin[i] = v
                  onChange({ origin })
                }}
              />
            </div>
          ))}
        </div>
      )}
      <Row>
        <label style={{ width: 60 }}>{t('pp.slice_field')}</label>
        <select
          id="dlg-slice-field"
          value={form.field}
          style={{ flex: 1, minWidth: 0 }}
          onChange={(e) => onChange({ field: e.target.value })}
        >
          {fields.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </Row>
      <Row>
        <label style={{ width: 60 }}>{t('pp.slice_colormap')}</label>
        <ColorMapSelect id="dlg-slice-cmap" value={form.colorMap} onChange={(v) => onChange({ colorMap: v })} />
      </Row>
      <div style={{ marginBottom: 4, fontWeight: 500 }}>{t('pp.slice_range')}</div>
      <Row>
        <label htmlFor="dlg-slice-range-mode">{t('pp.range_mode')}</label>
        <select id="dlg-slice-range-mode" value={isSliceAutoRange(form) ? 'auto' : 'manual'}
          onChange={(e) => onChange({ autoScale: e.target.value === 'auto' })}>
          <option value="auto">{t('pp.range_auto_from_data')}</option>
          <option value="manual">{t('pp.range_manual_literals')}</option>
        </select>
      </Row>
      <Row gap={4}>
        <label style={{ fontSize: '0.72rem' }}>Min:{rangeUnit}</label>
        {/* Shown in the user's unit and held in Kelvin, like the sidebar's
            boxes — a range typed in °C and applied as Kelvin is 273 K of
            colour scale away from what was asked for. */}
        <NumberField
          id="dlg-slice-clim-min"
          key={isTemperatureField(form.field) ? 'clim-min-' + displayTempUnit : 'clim-min-raw'}
          width={INPUT_WIDTH}
          decimals={2}
          value={isSliceAutoRange(form) ? null : toDisplay(form.field, form.climMin)}
          placeholder={t('pp.range_auto_from_data')}
          step={0.1}
          onCommit={(v) => onChange({ climMin: fromDisplay(form.field, v), autoScale: false })}
        />
        <label style={{ fontSize: '0.72rem' }}>Max:{rangeUnit}</label>
        <NumberField
          id="dlg-slice-clim-max"
          key={isTemperatureField(form.field) ? 'clim-max-' + displayTempUnit : 'clim-max-raw'}
          width={INPUT_WIDTH}
          decimals={2}
          value={isSliceAutoRange(form) ? null : toDisplay(form.field, form.climMax)}
          placeholder={t('pp.range_auto_from_data')}
          step={0.1}
          onCommit={(v) => onChange({ climMax: fromDisplay(form.field, v), autoScale: false })}
        />
      </Row>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button id="dlg-slice-cancel" className="btn" onClick={onClose}>
          {t('pp.cancel')}
        </button>
        <button id="dlg-slice-apply" className="btn primary" disabled={busy} onClick={onApply}>
          {t('pp.apply_slice')}
        </button>
      </div>
    </FloatingDialog>
  )
}

export interface StreamlineEditDialogProps {
  id: number
  form: StreamlineEditForm
  fields: string[]
  busy: boolean
  onChange: (patch: Partial<StreamlineEditForm>) => void
  onClose: () => void
  onApply: () => void
}

/** 编辑流线 — seed centre, radius, count and tube radius of one existing set. */
export function PpStreamlineEditDialog({
  id,
  form,
  fields,
  busy,
  onChange,
  onClose,
  onApply,
}: StreamlineEditDialogProps) {
  const { t } = useTranslation()
  return (
    <FloatingDialog
      id="pp-sl-edit-overlay"
      titlebarId="dlg-sl-titlebar"
      closeId="dlg-sl-close"
      title={`${t('pp.edit_streamline_title')} #${id}`}
      accent="var(--section-header-streamline,#98c379)"
      onClose={onClose}
    >
      <div style={{ marginBottom: 4, fontWeight: 500 }}>{t('pp.sl_center')}</div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 6 }}>
        {(['x', 'y', 'z'] as const).map((a, i) => (
          <div key={a} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 0 }}>
            <label style={{ width: 18 }}>{a.toUpperCase()}:</label>
            <NumberField
              id={`dlg-sl-c${a}`}
              width={INPUT_WIDTH}
              value={form.centre[i] as number}
              step={0.0001}
              onCommit={(v) => {
                const centre = [...form.centre] as Vec3
                centre[i] = v
                onChange({ centre })
              }}
            />
          </div>
        ))}
      </div>
      <Row>
        <label style={{ whiteSpace: 'nowrap' }}>{t('pp.sl_radius')}</label>
        <NumberField
          id="dlg-sl-r"
          width={INPUT_WIDTH}
          value={form.radius}
          step={0.0001}
          min={0.0001}
          onCommit={(v) => onChange({ radius: v })}
        />
        <label style={{ whiteSpace: 'nowrap' }}>{t('pp.sl_seeds')}</label>
        <NumberField
          id="dlg-sl-n"
          width={INPUT_WIDTH}
          value={form.count}
          step={1}
          min={1}
          onCommit={(v) => onChange({ count: Math.round(v) })}
        />
      </Row>
      <Row>
        <label style={{ whiteSpace: 'nowrap' }}>{t('pp.sl_tube')}</label>
        <NumberField
          id="dlg-sl-tube"
          width={INPUT_WIDTH}
          value={form.tubeRadius}
          step={0.005}
          min={0.001}
          onCommit={(v) => onChange({ tubeRadius: v })}
        />
      </Row>
      <Row>
        <label style={{ width: 50 }}>{t('pp.slice_field')}</label>
        <select
          id="dlg-sl-field"
          value={form.field}
          style={{ flex: 1, minWidth: 0 }}
          onChange={(e) => onChange({ field: e.target.value })}
        >
          {streamlineFieldOptions(fields).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </Row>
      <Row>
        <label style={{ width: 50 }}>{t('pp.slice_colormap')}</label>
        <ColorMapSelect id="dlg-sl-cmap" value={form.colorMap} onChange={(v) => onChange({ colorMap: v })} />
      </Row>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button id="dlg-sl-cancel" className="btn" onClick={onClose}>
          {t('pp.cancel')}
        </button>
        <button id="dlg-sl-apply" className="btn primary" disabled={busy} onClick={onApply}>
          {t('pp.apply_streamline')}
        </button>
      </div>
    </FloatingDialog>
  )
}
