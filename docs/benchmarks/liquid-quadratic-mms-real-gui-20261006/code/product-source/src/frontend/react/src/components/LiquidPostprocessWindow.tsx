import { useEffect, useRef, useState } from 'react'
import {
  closePpWindow,
  PP_TABS,
  ppWindowState,
  setPpWindowTab,
  subscribePpWindow,
  type PpTab,
  nudgePpFontScale,
  PP_FONT_STEP,
} from '../liquid/liquidPostprocess'
import type { TranslationKey } from '../i18n'
import { useTranslation } from '../useTranslation'
import { TimeSeriesChart } from './TimeSeriesChart'

/**
 * The liquid post-processing chart, floating over the viewport.
 *
 * Ports `_lsmCreatePostProcessWindow` (js/liquid_solve_monitor.js:899), ids
 * included — `lsm-monitor-window`, `lsm-chart-canvas`, `lsm-chart-legend`,
 * `lsm-pp-tab-*` — so one parity snippet reaches either app.
 *
 * Floating rather than docked for the same reason the air solver monitor is: the
 * curves are read against the geometry they came from, and a 640 px chart does
 * not fit a 340 px panel. It is dragged by its header and resized from any edge.
 *
 * Three tabs, not three charts, because they are three different UNITS —
 * kelvin, kg/s and pascals — and a shared y-axis across them would flatten the
 * temperature curve into the pressure one.
 */

const TAB_LABEL: Record<PpTab, TranslationKey> = {
  temperature: 'lsm.tab_temperature',
  flow: 'lsm.tab_flow',
  pressure: 'lsm.tab_pressure',
}

const TAB_ICON: Record<PpTab, string> = {
  temperature: '🌡',
  flow: '💧',
  pressure: '📏',
}

const HANDLES: { dir: string; style: React.CSSProperties }[] = [
  { dir: 'e', style: { top: 0, right: -4, width: 8, height: '100%', cursor: 'e-resize' } },
  { dir: 'w', style: { top: 0, left: -4, width: 8, height: '100%', cursor: 'w-resize' } },
  { dir: 's', style: { bottom: -4, left: 0, width: '100%', height: 8, cursor: 's-resize' } },
  { dir: 'n', style: { top: -4, left: 0, width: '100%', height: 8, cursor: 'n-resize' } },
  { dir: 'se', style: { bottom: -4, right: -4, width: 12, height: 12, cursor: 'se-resize' } },
  { dir: 'sw', style: { bottom: -4, left: -4, width: 12, height: 12, cursor: 'sw-resize' } },
  { dir: 'ne', style: { top: -4, right: -4, width: 12, height: 12, cursor: 'ne-resize' } },
  { dir: 'nw', style: { top: -4, left: -4, width: 12, height: 12, cursor: 'nw-resize' } },
]

const MIN_WIDTH = 420
const MIN_HEIGHT = 220

interface Box {
  left: number
  top: number
  width: number
  height: number
}

/** The legacy geometry: 640x400 at 20px from the right edge, 60px down. */
function initialBox(): Box {
  const width = 640
  const viewport = typeof window === 'undefined' ? 1280 : window.innerWidth
  return { left: Math.max(8, viewport - width - 20), top: 60, width, height: 400 }
}

interface Drag {
  dir: string | null
  startX: number
  startY: number
  box: Box
}

export function LiquidPostprocessWindow() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => subscribePpWindow(() => bump((n) => n + 1)), [])
  const state = ppWindowState()

  const [box, setBox] = useState<Box>(initialBox)
  const [collapsed, setCollapsed] = useState(false)
  const dragRef = useRef<Drag | null>(null)

  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const dx = e.clientX - drag.startX
      const dy = e.clientY - drag.startY
      const b = drag.box
      if (drag.dir === null) {
        setBox({ ...b, left: b.left + dx, top: b.top + dy })
        return
      }
      // Each edge moves its own side. Growing from the left or the top has to
      // move the origin as well, or the window would grow away from the handle
      // the user is holding.
      let { left, top, width, height } = b
      if (drag.dir.includes('e')) width = Math.max(MIN_WIDTH, b.width + dx)
      if (drag.dir.includes('s')) height = Math.max(MIN_HEIGHT, b.height + dy)
      if (drag.dir.includes('w')) {
        width = Math.max(MIN_WIDTH, b.width - dx)
        left = b.left + (b.width - width)
      }
      if (drag.dir.includes('n')) {
        height = Math.max(MIN_HEIGHT, b.height - dy)
        top = b.top + (b.height - height)
      }
      setBox({ left, top, width, height })
    }
    const onUp = () => {
      dragRef.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  if (!state.open) return null

  const start = (dir: string | null) => (e: React.PointerEvent) => {
    e.preventDefault()
    dragRef.current = { dir, startX: e.clientX, startY: e.clientY, box }
  }

  const series = state.datasets[state.tab]

  return (
    <div
      id="lsm-monitor-window"
      style={{
        position: 'fixed',
        zIndex: 900,
        left: box.left,
        top: box.top,
        width: box.width,
        height: collapsed ? undefined : box.height,
        minWidth: MIN_WIDTH,
        display: 'flex',
        flexDirection: 'column',
        background: '#1a1a2e',
        border: '1px solid #555',
        borderRadius: 6,
        boxShadow: '0 4px 20px rgba(0,0,0,0.6)',
        fontFamily: '"Share Tech Mono", monospace',
        fontSize: 12,
        color: '#ccc',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          padding: '6px 10px',
          background: '#222238',
          borderBottom: '1px solid #444',
          borderRadius: '6px 6px 0 0',
          cursor: 'move',
          userSelect: 'none',
          flexShrink: 0,
        }}
        onPointerDown={start(null)}
      >
        <span
          id="lsm-pp-collapse-btn"
          style={{ cursor: 'pointer', color: '#888', fontSize: 14, marginRight: 8 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? '▶' : '▼'}
        </span>
        <span style={{ flex: 1, fontWeight: 'bold', color: '#00b4dc', fontSize: 11 }}>
          📊 {state.title}
        </span>
        {/* A− / A+, as the legacy header has them
            (js/liquid_solve_monitor.js:1037-1048). The chart is read at arm's
            length on a projector, and 0.6x-2.0x is the range the legacy clamps
            to. `stopPropagation` on the pointer, or the press starts a
            window drag instead. */}
        <span
          id="lsm-pp-font-down"
          title="A-"
          style={{ cursor: 'pointer', color: '#888', fontSize: 12, marginRight: 6 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => nudgePpFontScale(-PP_FONT_STEP)}
        >
          A−
        </span>
        <span
          id="lsm-pp-font-up"
          title="A+"
          style={{ cursor: 'pointer', color: '#888', fontSize: 15, marginRight: 10 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => nudgePpFontScale(PP_FONT_STEP)}
        >
          A+
        </span>
        <span
          id="lsm-pp-close"
          style={{ cursor: 'pointer', color: '#e06c75', fontSize: 16 }}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={closePpWindow}
        >
          ×
        </span>
      </div>

      {!collapsed && (
        <div
          id="lsm-monitor-body"
          style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}
        >
          <div style={{ display: 'flex', borderBottom: '1px solid #333', flexShrink: 0 }}>
            {PP_TABS.map((tab) => {
              const active = state.tab === tab
              return (
                <button
                  key={tab}
                  id={`lsm-pp-tab-${tab}`}
                  onClick={() => setPpWindowTab(tab)}
                  style={{
                    flex: 1,
                    padding: '4px 0',
                    fontSize: 11,
                    border: 'none',
                    cursor: 'pointer',
                    background: active ? '#1a1a2e' : '#111',
                    color: active ? '#00b4dc' : '#666',
                    borderBottom: active ? '2px solid #00b4dc' : '2px solid transparent',
                  }}
                >
                  {TAB_ICON[tab]} {t(TAB_LABEL[tab])}
                </button>
              )
            })}
          </div>

          <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 6, padding: 6 }}>
            <div id="lsm-canvas-wrap" style={{ flex: 1, minWidth: 0, position: 'relative' }}>
              {state.loading ? (
                <div style={{ padding: 12, color: '#888' }}>{t('lsm.pp_loading')}</div>
              ) : state.error ? (
                <div style={{ padding: 12, color: '#e06c75' }}>{state.error}</div>
              ) : series.length === 0 ? (
                <div style={{ padding: 12, color: '#888' }} id="lsm-pp-no-data">
                  {t('lsm.pp_no_data_tab')}
                </div>
              ) : (
                <TimeSeriesChart
                  id="lsm-chart-canvas"
                  time={state.time}
                  series={series}
                  height={Math.max(120, box.height - 96)}
                  xLabel={t('lsm.pp_time_axis')}
                  fontScale={state.fontScale}
                />
              )}
            </div>
            <div
              id="lsm-chart-legend"
              style={{
                // The legend follows the same scale, as `_lsmUpdatePPLegend`
                // does (js/liquid_solve_monitor.js:1238) — a legend left at
                // 10px beside 2x axis labels is the half-done version of this.
                fontSize: Math.round(10 * state.fontScale),
                width: 130,
                flexShrink: 0,
                overflowY: 'auto',
                padding: 4,
                fontFamily: 'monospace',
                display: 'flex',
                flexDirection: 'column',
                gap: 2,
              }}
            >
              {series.map((s) => (
                <div
                  key={s.label}
                  data-series={s.label}
                  style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: 10,
                      height: 3,
                      marginTop: 6,
                      flexShrink: 0,
                      background: s.color,
                      borderRadius: 999,
                    }}
                  />
                  <span style={{ minWidth: 0, overflowWrap: 'anywhere', lineHeight: 1.3 }}>
                    {s.label}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {!collapsed &&
        HANDLES.map((h) => (
          <div
            key={h.dir}
            data-resize={h.dir}
            style={{ position: 'absolute', zIndex: 10, ...h.style }}
            onPointerDown={start(h.dir)}
          />
        ))}
    </div>
  )
}
