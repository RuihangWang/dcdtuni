import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { TranslationKey } from '../i18n'
import * as store from '../projectStore'
import {
  bumpMonitorFont,
  closeSolverMonitor,
  getSolverMonitor,
  MIN_HEIGHT,
  MIN_WIDTH,
  setMonitorCollapsed,
  setMonitorGeometry,
  setMonitorTab,
  subscribeSolverMonitor,
  toggleMonitorSeries,
  type WindowGeometry,
} from '../run/solverMonitor'
import { legendLabel, SERIES_COLORS, seriesColor, type ChartTab } from '../run/solverChart'
import { drawSolverChart } from '../run/solverChartDraw'
import { useTranslation } from '../useTranslation'

/**
 * The floating solver monitor.
 *
 * What a user watches a solve FOR: residuals falling (or not), and the patch
 * temperatures settling. Reading that off scrolling log text is the thing this
 * window exists to replace.
 *
 * Floating and draggable rather than docked in a panel, exactly as
 * `_smCreateMonitorWindow` (STL_generate.html:25103) builds it: the plot has to
 * be readable while the viewport shows the geometry the numbers belong to, and
 * a 640px chart in a 340px column is not.
 *
 * Ids match the legacy window's — `sm-chart-canvas`, `sm-chart-legend`,
 * `sm-tab-residual` and the rest — so one parity snippet addresses either app.
 */

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

const TAB_ACTIVE: React.CSSProperties = {
  padding: '3px 12px',
  fontSize: 11,
  background: '#2a3a4a',
  border: '1px solid #4a6a8a',
  color: '#8be9fd',
  cursor: 'pointer',
  borderRadius: 3,
}
const TAB_INACTIVE: React.CSSProperties = {
  ...TAB_ACTIVE,
  background: '#1a2a3a',
  border: '1px solid #333',
  color: '#888',
}

const WAITING_KEY: Record<ChartTab, TranslationKey> = {
  residual: 'sm.waiting_residual',
  temperature: 'sm.waiting_temp',
  sensor: 'sm.waiting_sensor',
}

interface Drag {
  kind: 'move' | 'resize'
  dir: string
  startX: number
  startY: number
  start: WindowGeometry
}

export function SolverMonitorWindow() {
  const { t } = useTranslation()
  const [, bump] = useState(0)
  useEffect(() => subscribeSolverMonitor(() => bump((n) => n + 1)), [])
  const mon = getSolverMonitor()

  // The live rectangle during a drag. Committed to the store on release so a
  // move does not push a redraw through every listener sixty times a second.
  // Mirrored into a ref because the pointerup handler needs the LAST rectangle,
  // and reading it out of a state updater would make that updater impure.
  const [live, setLive] = useState<WindowGeometry | null>(null)
  const liveRef = useRef<WindowGeometry | null>(null)
  const setLiveGeometry = useCallback((g: WindowGeometry | null) => {
    liveRef.current = g
    setLive(g)
  }, [])
  const dragRef = useRef<Drag | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [names, setNames] = useState<string[]>([])

  const geometry = live ?? mon.geometry

  const onPointerDown = useCallback(
    (e: React.PointerEvent, kind: 'move' | 'resize', dir = '') => {
      // Buttons and the legend own their clicks; dragging from them would make
      // "hide this series" a one-pixel window move instead.
      if (kind === 'move') {
        const el = e.target as HTMLElement
        if (el.closest('button') || el.closest('#sm-chart-legend') || el.closest('input')) return
      }
      dragRef.current = {
        kind,
        dir,
        startX: e.clientX,
        startY: e.clientY,
        start: { ...(live ?? mon.geometry) },
      }
      setLiveGeometry({ ...(live ?? mon.geometry) })
      e.preventDefault()
      e.stopPropagation()
    },
    [live, mon.geometry, setLiveGeometry],
  )

  useEffect(() => {
    function move(e: PointerEvent) {
      const drag = dragRef.current
      if (!drag) return
      const dx = e.clientX - drag.startX
      const dy = e.clientY - drag.startY
      const s = drag.start
      if (drag.kind === 'move') {
        setLiveGeometry({ ...s, left: s.left + dx, top: s.top + dy })
        return
      }
      let { left, top, width, height } = s
      const d = drag.dir
      if (d.includes('e')) width = Math.max(MIN_WIDTH, s.width + dx)
      if (d.includes('w')) {
        width = Math.max(MIN_WIDTH, s.width - dx)
        left = s.left + s.width - width
      }
      if (d.includes('s')) height = Math.max(MIN_HEIGHT, s.height + dy)
      if (d.includes('n')) {
        height = Math.max(MIN_HEIGHT, s.height - dy)
        top = s.top + s.height - height
      }
      setLiveGeometry({ left, top, width, height })
    }
    function up() {
      const drag = dragRef.current
      dragRef.current = null
      if (!drag) return
      const final = liveRef.current
      setLiveGeometry(null)
      if (final) setMonitorGeometry(final)
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [setLiveGeometry])

  /**
   * Sensor curves keep the colour their marker has in the viewport.
   *
   * Position in the probe list, not position in the plot: a probe that has not
   * reported yet is absent from the chart, and colouring by chart order would
   * shift every other curve's colour the moment it appears.
   */
  const sensorColor = useCallback((name: string) => {
    const probes = store.sensorProbes(store.getProject())
    const idx = probes.findIndex((p) => p.meta.name === name)
    return idx >= 0 ? (SERIES_COLORS[idx % SERIES_COLORS.length] as string) : '#ccc'
  }, [])

  const datasets =
    mon.tab === 'residual'
      ? mon.data.residuals
      : mon.tab === 'sensor'
        ? mon.data.sensors
        : mon.data.temperatures

  // Layout effect: the canvas has to be sized from the wrapper AFTER the window
  // has its new size, or a resize draws the old plot stretched.
  useLayoutEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = window.devicePixelRatio || 1
    const w = wrap.clientWidth
    const h = wrap.clientHeight
    if (w < 10 || h < 10) return
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
    canvas.style.width = `${w}px`
    canvas.style.height = `${h}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const drawn = drawSolverChart({
      ctx,
      width: w,
      height: h,
      tab: mon.tab,
      datasets,
      hidden: mon.hidden,
      fontScale: mon.fontScale,
      waitingText: t(WAITING_KEY[mon.tab]),
      sensorColor,
    })
    setNames((prev) => (prev.length === drawn.length && prev.every((n, i) => n === drawn[i]) ? prev : drawn))
  }, [mon.revision, mon.tab, mon.hidden, mon.fontScale, mon.collapsed, geometry, datasets, sensorColor, t])

  if (!mon.open) return null

  const notFound = Object.keys(mon.data.sensorNotFound).filter((n) => !names.includes(n))

  return (
    <div
      id="sm-monitor-window"
      onPointerDown={(e) => onPointerDown(e, 'move')}
      style={{
        position: 'fixed',
        zIndex: 900,
        top: geometry.top,
        left: geometry.left,
        width: geometry.width,
        height: mon.collapsed ? 'auto' : geometry.height,
        minWidth: MIN_WIDTH,
        minHeight: mon.collapsed ? 0 : MIN_HEIGHT,
        display: 'flex',
        flexDirection: 'column',
        cursor: 'move',
        background: '#1a1a2e',
        border: '1px solid #555',
        borderRadius: 6,
        boxShadow: '0 4px 20px rgba(0,0,0,0.6)',
        fontFamily: '"Share Tech Mono",monospace',
        fontSize: 12,
        color: '#ccc',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '6px 10px',
          flexShrink: 0,
          background: '#222238',
          borderBottom: '1px solid #444',
          borderRadius: '6px 6px 0 0',
          cursor: 'move',
          userSelect: 'none',
        }}
      >
        <button
          id="sm-collapse-btn"
          onClick={() => setMonitorCollapsed(!mon.collapsed)}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#888', fontSize: 14, marginRight: 8 }}
        >
          {mon.collapsed ? '▶' : '▼'}
        </button>
        <span style={{ flex: 1, fontWeight: 'bold', color: '#fff' }}>{t('sm.window_title')}</span>
        <button
          id="sm-font-down"
          title="Font −"
          onClick={() => bumpMonitorFont(-0.1)}
          style={{ cursor: 'pointer', color: '#888', fontSize: 14, marginRight: 4, padding: '0 4px', border: '1px solid #555', borderRadius: 3, background: 'none' }}
        >
          A−
        </button>
        <button
          id="sm-font-up"
          title="Font +"
          onClick={() => bumpMonitorFont(0.1)}
          style={{ cursor: 'pointer', color: '#888', fontSize: 14, marginRight: 8, padding: '0 4px', border: '1px solid #555', borderRadius: 3, background: 'none' }}
        >
          A+
        </button>
        <button
          id="sm-close-btn"
          onClick={closeSolverMonitor}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#888', fontSize: 16 }}
        >
          ×
        </button>
      </div>

      {!mon.collapsed && (
        <div
          id="sm-monitor-body"
          style={{ padding: 8, flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        >
          <div style={{ display: 'flex', gap: 4, marginBottom: 6, flexShrink: 0 }}>
            <button
              id="sm-tab-residual"
              className="sm-tab-btn"
              style={mon.tab === 'residual' ? TAB_ACTIVE : TAB_INACTIVE}
              onClick={() => setMonitorTab('residual')}
            >
              {t('sm.tab_residual')}
            </button>
            {/* The temperature tab appears only when patches were picked, the
                way the legacy passes `hasTemperature` into the window: an
                always-present tab that can only ever say "waiting" reads as a
                monitor that is broken. */}
            {mon.hasTemperature && (
              <button
                id="sm-tab-temp"
                className="sm-tab-btn"
                style={mon.tab === 'temperature' ? TAB_ACTIVE : TAB_INACTIVE}
                onClick={() => setMonitorTab('temperature')}
              >
                {t('sm.tab_temp')}
              </button>
            )}
            <button
              id="sm-tab-sensor"
              className="sm-tab-btn"
              style={mon.tab === 'sensor' ? TAB_ACTIVE : TAB_INACTIVE}
              onClick={() => setMonitorTab('sensor')}
            >
              {t('sm.tab_sensor_label')}
            </button>
          </div>

          <div
            id="sm-monitor-content"
            style={{ flex: 1, minHeight: 0, display: 'flex', gap: 8, overflow: 'hidden', alignItems: 'stretch' }}
          >
            <div
              id="sm-canvas-wrap"
              ref={wrapRef}
              style={{ flex: '1 1 auto', minWidth: 0, minHeight: 120, position: 'relative' }}
            >
              <canvas
                id="sm-chart-canvas"
                ref={canvasRef}
                style={{
                  background: '#0d0d1a',
                  border: '1px solid #333',
                  borderRadius: 3,
                  display: 'block',
                  width: '100%',
                  height: '100%',
                }}
              />
            </div>
            <div
              id="sm-chart-legend"
              style={{
                width: 190,
                minWidth: 160,
                maxWidth: 240,
                padding: '6px 8px',
                fontSize: Math.round(10 * mon.fontScale),
                color: '#888',
                display: 'flex',
                flexDirection: 'column',
                gap: 6,
                overflowY: 'auto',
                overflowX: 'hidden',
                flexShrink: 0,
                background: '#101522',
                border: '1px solid #333',
                borderRadius: 3,
              }}
            >
              {names.length === 0 && (
                <div style={{ color: '#666', lineHeight: 1.4 }}>{t(WAITING_KEY[mon.tab])}</div>
              )}
              {names.map((name, i) => (
                <div
                  key={name}
                  data-series={name}
                  onClick={() => toggleMonitorSeries(name)}
                  style={{
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 8,
                    width: '100%',
                    minWidth: 0,
                    padding: '4px 6px',
                    border: '1px solid #2f3448',
                    borderRadius: 4,
                    background: '#151a28',
                    opacity: mon.hidden[name] ? 0.3 : 1,
                  }}
                >
                  <span
                    style={{
                      display: 'inline-block',
                      width: 12,
                      height: 4,
                      marginTop: 7,
                      flexShrink: 0,
                      background: seriesColor(mon.tab, name, i, sensorColor),
                      borderRadius: 999,
                    }}
                  />
                  <span style={{ minWidth: 0, overflowWrap: 'anywhere', wordBreak: 'break-word', lineHeight: 1.35 }}>
                    {legendLabel(name)}
                  </span>
                </div>
              ))}
              {mon.tab !== 'residual' &&
                notFound.map((name) => (
                  <div
                    key={`nf-${name}`}
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 8,
                      width: '100%',
                      minWidth: 0,
                      padding: '4px 6px',
                      border: '1px solid #5a2020',
                      borderRadius: 4,
                      background: '#2a1010',
                      opacity: 0.8,
                    }}
                  >
                    <span
                      style={{
                        display: 'inline-block',
                        width: 12,
                        height: 4,
                        marginTop: 7,
                        flexShrink: 0,
                        background: sensorColor(name),
                        borderRadius: 999,
                        opacity: 0.5,
                      }}
                    />
                    <span style={{ minWidth: 0, overflowWrap: 'anywhere', lineHeight: 1.35, color: '#f88' }}>
                      {name} ({t('sensor.not_found')})
                    </span>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}

      {!mon.collapsed &&
        HANDLES.map((h) => (
          <div
            key={h.dir}
            data-resize={h.dir}
            onPointerDown={(e) => onPointerDown(e, 'resize', h.dir)}
            style={{ position: 'absolute', zIndex: 10, ...h.style }}
          />
        ))}
    </div>
  )
}
