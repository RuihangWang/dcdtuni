import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { COLOR_MAPS, colormapData, type ColorMap } from '../postprocess/ppColormaps'
import {
  isTemperatureField,
  formatColorbarTick,
  scalarFieldUnit,
  k2display,
  display2k,
  setTempUnit,
  subscribeTempUnit,
  tempUnit,
  tempUnitLabel,
  type TempUnit,
} from '../postprocess/ppUnits'
import { useTranslation } from '../useTranslation'

/**
 * The floating colorbar beside the viewport.
 *
 * A port of `_ppCreateColorbarWidget` (STL_generate.html:19933): a draggable
 * 30x200 bar, the field name and the two limits around it, and a settings
 * dialog on double-click carrying the range, the colour map and — for
 * temperature — the display unit.
 *
 * ## Why it is a canvas and not a CSS gradient
 *
 * The bar is painted row by row from the SAME 256-entry table the shader
 * samples. A CSS `linear-gradient` interpolates in its own space between the
 * stops it is given, so a 16-stop approximation of coolwarm drifts from the
 * picture in the middle of each segment — and a legend that disagrees with the
 * render is worse than no legend.
 *
 * ## Why it renders into document.body
 *
 * `position: fixed` is resolved against the nearest ancestor with a transform,
 * filter or backdrop-filter, and the panels around the viewport have all three.
 * Left in the panel subtree the bar would be clipped to it. The legacy widget
 * appends to `document.body` for the same reason.
 */

export interface ColorbarOverlayProps {
  /** Field label without a unit, e.g. `T` or `SL#1 (|U|)`. */
  label: string
  /** The field, so a temperature gets converted and everything else does not. */
  field: string
  colorMap: ColorMap
  /** Colour limits in RAW units (Kelvin for T). */
  climMin: number
  climMax: number
  /** Distance from the right edge, so several bars sit side by side. */
  rightOffset?: number
  onApply(min: number, max: number, colorMap: ColorMap): void
  /** Offered in the dialog when the caller can compute the data range. */
  onAutoScale?: () => { min: number; max: number } | null
  /** A stable id, so tests and the harness can address one bar of several. */
  id?: string
}

export function ColorbarOverlay({
  label,
  field,
  colorMap,
  climMin,
  climMax,
  rightOffset = 320,
  onApply,
  onAutoScale,
  id,
}: ColorbarOverlayProps) {
  const { t } = useTranslation()
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const barRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const [dialog, setDialog] = useState(false)
  const unit = useSyncExternalStore(subscribeTempUnit, tempUnit)
  const isTemp = isTemperatureField(field)
  const toDisp = (raw: number) => (isTemp ? k2display(raw, unit) : raw)
  const toRaw = (shown: number) => (isTemp ? display2k(shown, unit) : shown)

  // Dialog fields, seeded from the current limits each time it opens.
  const [dlgMin, setDlgMin] = useState('')
  const [dlgMax, setDlgMax] = useState('')
  const [dlgMap, setDlgMap] = useState<ColorMap>(colorMap)

  const dialogUnit = useRef(unit)
  useLayoutEffect(() => {
    const previous = dialogUnit.current
    dialogUnit.current = unit
    if (!dialog || !isTemp || previous === unit) return
    // Every open dialog follows the global unit, including its pending edits.
    // Convert from its previous unit before paint; do not reinterpret the text.
    const convertDraft = (text: string) => {
      if (text.trim() === '') return text
      const shown = Number(text)
      return Number.isFinite(shown)
        ? String(k2display(display2k(shown, previous), unit))
        : text
    }
    setDlgMin(convertDraft)
    setDlgMax(convertDraft)
  }, [unit, dialog, isTemp])

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!ctx) return
    const data = colormapData(colorMap)
    // Top row is the MAXIMUM. Painting it the other way up puts the legend
    // upside down while still looking like a colorbar.
    for (let y = 0; y < 196; y++) {
      const v = 1.0 - y / 195
      const i = Math.floor(v * 255) * 4
      ctx.fillStyle = `rgb(${data[i]},${data[i + 1]},${data[i + 2]})`
      ctx.fillRect(0, y, 26, 1)
    }
  }, [colorMap])

  // Dragging. The listeners go on the document, not the bar: the pointer
  // routinely leaves a 30px-wide element mid-drag, and a bar-local listener
  // would drop the drag the moment it did.
  useEffect(() => {
    const bar = barRef.current
    if (!bar) return
    let dragging = false
    let offX = 0
    let offY = 0
    const down = (e: MouseEvent) => {
      if (e.detail >= 2) return // let the double-click open the dialog
      const rect = bar.getBoundingClientRect()
      dragging = true
      offX = e.clientX - rect.left
      offY = e.clientY - rect.top
      bar.style.cursor = 'grabbing'
      e.preventDefault()
    }
    const move = (e: MouseEvent) => {
      if (!dragging) return
      setPos({ left: e.clientX - offX, top: e.clientY - offY })
    }
    const up = () => {
      if (!dragging) return
      dragging = false
      bar.style.cursor = 'grab'
    }
    bar.addEventListener('mousedown', down)
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
    return () => {
      bar.removeEventListener('mousedown', down)
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
  }, [])

  function openDialog() {
    dialogUnit.current = unit
    setDlgMin(String(toDisp(climMin)))
    setDlgMax(String(toDisp(climMax)))
    setDlgMap(colorMap)
    setDialog(true)
  }

  const fieldUnit = isTemp ? tempUnitLabel(unit) : scalarFieldUnit(field)
  const barLabel = fieldUnit ? label + ' (' + fieldUnit + ')' : label
  const tick = (raw: number) => formatColorbarTick(toDisp(raw), toDisp(climMin), toDisp(climMax))
  const unitSuffix = isTemp ? ` ${tempUnitLabel(unit)}` : ''
  const style: React.CSSProperties = pos
    ? { left: pos.left, top: pos.top, right: 'auto', transform: 'none' }
    : { right: rightOffset, top: '50%', transform: 'translateY(-50%)' }

  return createPortal(
    <>
      <div
        ref={barRef}
        className="pp-colorbar"
        id={id}
        style={style}
        onDoubleClick={(e) => {
          e.stopPropagation()
          openDialog()
        }}
      >
        <div className="pp-colorbar-field" title={barLabel}>{barLabel}</div>
        <div className="pp-colorbar-max">
          {tick(climMax)}
          {unitSuffix}
        </div>
        <canvas ref={canvasRef} width={26} height={196} />
        <div className="pp-colorbar-min">
          {tick(climMin)}
          {unitSuffix}
        </div>
      </div>

      {dialog && (
        <div className="pp-colorbar-dialog" id={id ? `${id}-dialog` : undefined}>
          <div className="pp-cbw-head">
            <span>{barLabel}</span>
            <button type="button" className="pp-cbw-close" aria-label={t('pp.cancel')} onClick={() => setDialog(false)}>
              ×
            </button>
          </div>

          {isTemp && (
            <div className="pp-cbw-field">
              <label>{t('pp.temp_unit')}</label>
              <select
                className="pp-cbw-unit"
                value={unit}
                onChange={(e) => {
                  // The subscription converts every open dialog exactly once.
                  setTempUnit(e.target.value as TempUnit)
                }}
              >
                <option value="C">{t('pp.celsius')}</option>
                <option value="K">{t('pp.kelvin')}</option>
                <option value="F">{t('pp.fahrenheit')}</option>
              </select>
            </div>
          )}

          <label className="pp-cbw-label">{t('pp.display_range')}</label>
          <div className="pp-cbw-row">
            <label>Min:</label>
            <input
              type="number"
              className="pp-cbw-min"
              step="any"
              value={dlgMin}
              onChange={(e) => setDlgMin(e.target.value)}
            />
          </div>
          <div className="pp-cbw-row">
            <label>Max:</label>
            <input
              type="number"
              className="pp-cbw-max"
              step="any"
              value={dlgMax}
              onChange={(e) => setDlgMax(e.target.value)}
            />
          </div>

          <div className="pp-cbw-field">
            <label>{t('pp.sl_colormap')}</label>
            <select
              className="pp-cbw-cmap"
              value={dlgMap}
              onChange={(e) => setDlgMap(e.target.value as ColorMap)}
            >
              {COLOR_MAPS.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          <div className="pp-cbw-buttons">
            {onAutoScale && (
              <button
                className="pp-cbw-auto"
                onClick={() => {
                  const r = onAutoScale()
                  if (!r) return
                  setDlgMin(String(toDisp(r.min)))
                  setDlgMax(String(toDisp(r.max)))
                }}
              >
                {t('pp.auto_scale')}
              </button>
            )}
            <button
              className="pp-cbw-apply"
              onClick={() => {
                const min = Number(dlgMin)
                const max = Number(dlgMax)
                // A non-numeric or inverted range is rejected rather than
                // applied: min > max makes the shader's divisor negative and
                // every colour comes out at one end.
                if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) return
                onApply(toRaw(Math.min(min, max)), toRaw(Math.max(min, max)), dlgMap)
                setDialog(false)
              }}
            >
              {t('common.apply')}
            </button>
          </div>
        </div>
      )}
    </>,
    document.body,
  )
}
