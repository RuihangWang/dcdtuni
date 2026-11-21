/**
 * Omniverse colours a liquid result itself (restored 2026-10-22; the original
 * caller, OvLiquidSection, went with the standalone OV panel in 662252778).
 *
 * The browser relays a server-signed grant and frame numbers; Kit re-hashes
 * the granted result.json, binds it to the scene it acknowledged and computes
 * every colour with the rules this editor uses (liquid_colour.py, pinned by
 * the shared contracts). What the browser adds per frame is the display
 * choice — the quantity, the colour map and the two scales of the Three.js
 * legends — so both viewports show the same picture on the same scale.
 *
 * While Kit colours (`liquidActive`), the result sample no longer enters the
 * Omniverse scene key: the timeline drives setLiquidFrame, latest wins, and
 * the scene is not resent (~86 s on Shinaide). A run that gains samples (a
 * running experiment) or another run is granted again.
 */

import type { OvCommandClient, OvDisplayGrant, OvLiquidFrame, OvLiquidInfo } from '../renderer/ovProtocol'
import { ovSession, setOvSession } from '../renderer/ovSession'
import { arrowScales, chipScale, liquidDisplayOptions, networkScale, subscribeLiquidDisplay, type ArrowScales, type LiquidDisplayOptions } from './liquidDisplayState'
import { liquidRunState, subscribeLiquid } from './liquidSession'
import { selectedResultSample, subscribeResultSample } from './liquidTempColor'

type Bag = Record<string, unknown>
export interface OvLiquidPaint {
  domains: { network?: [number, number]; chips?: [number, number] } | null
  display: { quantity: string; colormap: string; arrows: OvArrowOptions | null }
}
/** display.arrows of setLiquidFrame (Kit liquid_arrows.parse_arrow_options). */
export interface OvArrowOptions {
  density: number; speedScale: number; animate: boolean; colour: 'velocity' | 'plain'
  mass: [number, number] | null; velocity: [number, number] | null; colormap: string
}
/** Kit draws the arrows with the editor's options and on its legends' scales. */
export function ovArrowOptions(opts: Pick<LiquidDisplayOptions, 'arrows' | 'density' | 'speedScale' | 'animate' | 'arrowColor'>,
  scales: Pick<ArrowScales, 'mass' | 'velocity' | 'colorMap'>): OvArrowOptions | null {
  if (!opts.arrows) return null
  return {
    density: opts.density, speedScale: opts.speedScale, animate: opts.animate, colour: opts.arrowColor,
    mass: scales.mass, velocity: scales.velocity, colormap: scales.colorMap,
  }
}
export interface OvLiquidDeps {
  client: () => Pick<OvCommandClient, 'loadLiquidResult' | 'setLiquidFrame' | 'clearLiquidColours'> | null
  results: () => Bag | null
  sample: () => number
  paint: (raw: Bag, sample: number) => OvLiquidPaint
  grant: (runId: string) => Promise<OvDisplayGrant>
  sceneIdle: () => (() => Promise<void>) | null
  setActive: (on: boolean) => void
}
export type OvLiquidStatus = 'off' | 'loading' | 'on' | 'unbound' | 'error'
export interface OvLiquidState { status: OvLiquidStatus; info: OvLiquidInfo | null; frame: OvLiquidFrame | null; note: string }

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** POST /api/ov/liquid-result-grant: the server signs a display grant for a DAE run of the selected case. */
export async function requestLiquidResultGrant(runId: string, fetchImpl: typeof fetch = fetch): Promise<OvDisplayGrant> {
  const url = '/api/ov/liquid-result-grant'
  const resp = await fetchImpl(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ runId }) })
  const data = (await resp.json().catch(() => ({}))) as Bag
  if (!resp.ok) {
    const missing = Array.isArray(data.missing) ? ` (${(data.missing as string[]).join(', ')})` : ''
    throw new Error((typeof data.error === 'string' ? data.error : `${url} -> ${resp.status}`) + missing)
  }
  const grant = data.grant as OvDisplayGrant | undefined
  if (!grant || typeof grant.signature !== 'string') throw new Error('server returned no display grant')
  return grant
}

export class OvLiquidColouring {
  state: OvLiquidState = { status: 'off', info: null, frame: null, note: '' }
  private enabled = false
  private loaded: { runId: string; samples: number } | null = null
  private loading: Promise<void> | null = null
  private painting = false
  private repaint = false
  private generation = 0
  private listeners = new Set<() => void>()
  constructor(private deps: OvLiquidDeps) {}

  subscribe(fn: () => void): () => void { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  get isEnabled(): boolean { return this.enabled }
  private set(patch: Partial<OvLiquidState>) { this.state = { ...this.state, ...patch }; this.listeners.forEach(fn => fn()) }

  private target(): { raw: Bag; runId: string; samples: number } | null {
    const raw = this.deps.results()
    const runId = typeof raw?.run_id === 'string' ? raw.run_id : null
    if (!raw || !runId) return null
    return { raw, runId, samples: Array.isArray(raw.time_s) ? raw.time_s.length : 0 }
  }

  async enable(): Promise<void> {
    this.enabled = true
    this.loaded = null
    await this.sync()
  }

  async disable(): Promise<void> {
    this.enabled = false
    const gen = ++this.generation
    const client = this.deps.client()
    this.loaded = null
    try { if (client && this.state.status === 'on') await client.clearLiquidColours() } catch { /* the scene resync restores it */ }
    if (gen !== this.generation) return
    this.deps.setActive(false)
    this.set({ status: 'off', info: null, frame: null, note: '' })
  }

  /** The OV session ended: forget what Kit held (it no longer exists). */
  reset(): void {
    this.generation++
    this.loaded = null
    this.set({ status: this.enabled ? 'loading' : 'off', info: null, frame: null })
  }

  /** Bring Kit up to date: load the shown run if it is new or longer, then paint the shown sample. */
  async sync(): Promise<void> {
    if (!this.enabled) return
    const target = this.target()
    if (!target) return
    if (!target.raw.object_bindings || !Array.isArray(target.raw.cell_edge_ids)) {
      this.deps.setActive(false)
      this.set({ status: 'unbound', info: null, frame: null })
      return
    }
    if (!this.loaded || this.loaded.runId !== target.runId || target.samples > this.loaded.samples) {
      if (this.loading) { await this.loading; return this.sync() }
      this.loading = this.load(target.runId).finally(() => { this.loading = null })
      await this.loading
      if (!this.loaded) return
    }
    await this.paint()
  }

  private async load(runId: string): Promise<void> {
    const gen = ++this.generation
    const client = this.deps.client()
    if (!client) return
    this.set({ status: 'loading', note: '' })
    try {
      const idle = this.deps.sceneIdle()
      if (idle) await idle()
      if (gen !== this.generation) return
      const grant = await this.deps.grant(runId)
      if (gen !== this.generation) return
      const ack = await client.loadLiquidResult(grant)
      if (gen !== this.generation) return
      if (!ack.liquid || ack.liquid.runId !== runId) throw new Error('Kit acknowledged a different liquid run')
      this.loaded = { runId, samples: ack.liquid.samples }
      this.deps.setActive(true)
      this.set({ status: 'on', info: ack.liquid })
    } catch (e) {
      if (gen !== this.generation) return
      this.loaded = null
      this.deps.setActive(false)
      this.set({ status: 'error', note: errText(e) })
    }
  }

  private async paint(): Promise<void> {
    if (this.painting) { this.repaint = true; return }
    const client = this.deps.client()
    const target = this.target()
    if (!client || !target || !this.loaded) return
    this.painting = true
    const gen = this.generation
    try {
      const sample = this.deps.sample()
      const { domains, display } = this.deps.paint(target.raw, sample)
      const ack = await client.setLiquidFrame(sample, domains, display)
      if (gen === this.generation && ack.liquidFrame) this.set({ frame: ack.liquidFrame, note: '' })
    } catch (e) {
      if (gen === this.generation) this.set({ note: errText(e) })
    } finally {
      this.painting = false
      if (this.repaint) { this.repaint = false; void this.paint() }
    }
  }
}

let app: OvLiquidColouring | null = null
/** The editor's instance: the OV session's client, the shown result, the 3D view's scales. */
export function appOvLiquidColouring(): OvLiquidColouring {
  app ??= new OvLiquidColouring({
    client: () => { const s = ovSession(); return s.mode === 'ov' && s.ready && !s.degraded ? s.client : null },
    results: () => liquidRunState().results as Bag | null,
    sample: () => selectedResultSample(),
    paint: (raw, sample) => {
      const network = networkScale(raw, sample), chips = chipScale(raw, sample)
      return {
        domains: { ...(network.domain ? { network: network.domain } : {}), ...(chips ? { chips } : {}) },
        display: {
          quantity: network.quantity, colormap: network.colorMap,
          arrows: ovArrowOptions(liquidDisplayOptions(), arrowScales(raw, sample)),
        },
      }
    },
    grant: runId => requestLiquidResultGrant(runId),
    sceneIdle: () => ovSession().sceneIdle,
    setActive: on => { if (ovSession().liquidActive !== on) setOvSession({ liquidActive: on }) },
  })
  return app
}

let wanted = true
/** The 3D display's "colour in Omniverse" switch; on until turned off. */
export function ovLiquidWanted(): boolean { return wanted }
export function setOvLiquidWanted(on: boolean): void { wanted = on }

/**
 * The liquid channel of an Omniverse session (renderer/displayDistributor):
 * from the scene acknowledgement until the session ends, Kit colours the
 * shown liquid result and follows its sample, display choices and run --
 * whatever panel is open. Returns the channel's teardown.
 */
export function startOvLiquidChannel(): () => void {
  const colouring = appOvLiquidColouring()
  colouring.reset()
  if (wanted) void colouring.enable()
  const sync = () => { if (colouring.isEnabled) void colouring.sync() }
  const offs = [subscribeResultSample(sync), subscribeLiquidDisplay(sync), subscribeLiquid(sync)]
  return () => offs.forEach(off => off())
}
