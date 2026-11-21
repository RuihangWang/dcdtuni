import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type {
  OvCommandClient, OvDatasetInfo, OvLiquidFrame, OvLiquidInfo, OvSliceParams, OvSliceResult, OvVolumeState,
} from '../renderer/ovProtocol'
import { listOvRuns, requestLiquidResultGrant, requestOvDatasetGrant, type OvRun } from '../renderer/ovDataset'
import { OV_LIQUID_RAMP, OV_SLICE_RAMP, OV_VOLUME_RAMP, sampleRamp, type Ramp } from '../renderer/ovColormaps'
import { airDisplayPreference, selectedAirDisplayRun, rememberAirDisplay, airFramePreference, rememberAirFrame, type AirDisplayPreference, liquidDisplayPreference, rememberLiquidDisplay } from '../renderer/ovDisplayPreferences'
import { ovSession, setOvSession } from '../renderer/ovSession'
import { fetchLiquidResults, liquidRunState, subscribeLiquid } from '../liquid/liquidSession'
import { runtimeState, subscribeRuntime } from '../liquid/liquidRuntimeClient'
import { getCaseStatus, getServerInfo, subscribeSession, type CaseStatus, type ServerInfo } from '../run/session'
import { liquidGlobalDomains, selectedResultSample, setResultSample, subscribeResultSample } from '../liquid/liquidTempColor'
import { useTranslation } from '../useTranslation'
import * as store from '../projectStore'
import { nearestIndex, projectIsTransient } from '../transient/resultPlayback'
import { currentLiveAirResultSelection, currentResultLayerContext } from '../renderer/resultLayerRevision'

/** OV-side post-processing controls (display-only, Stage 3).
 *
 * Mounted by RendererControl while Omniverse owns the viewport. The result
 * dataset, the volume and the slice are acked render-path commands: the
 * panel shows what Kit reports it rendered (colour domain, triangle count,
 * adopted drag), never what it merely asked for. The dataset is chosen by run
 * identity; the server converts it and signs the grant Kit verifies, so the
 * browser never names a file. Streamlines and the seed sphere still ride the
 * reference demo's fire-and-forget channel and are labelled as unacknowledged.
 */
export interface OvPostProcessPanelProps {
  client: OvCommandClient
  degraded: boolean
  projectUid?: string | null
}

type Vec3 = [number, number, number]

const NORMALS: { key: 'y' | 'yneg' | 'x' | 'z'; label: string; value: Vec3 }[] = [
  { key: 'y', label: '+Y', value: [0, 1, 0] },
  { key: 'yneg', label: '−Y', value: [0, -1, 0] },
  { key: 'x', label: '+X', value: [1, 0, 0] },
  { key: 'z', label: '+Z', value: [0, 0, 1] },
]

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const round3 = (v: number) => Math.round(v * 1000) / 1000
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(1) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3))

/** Legend painted from the SAME ramp Kit renders with (see ovColormaps). */
export function OvLegend({ ramp, lo, hi, label, uniform }: { ramp: Ramp; lo: number; hi: number; label: string; uniform?: string }) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  useEffect(() => {
    const ctx = canvas.current?.getContext?.('2d')
    if (!ctx) return
    const w = canvas.current!.width
    for (let x = 0; x < w; x++) {
      const [r, g, b] = sampleRamp(ramp, x / (w - 1))
      ctx.fillStyle = `rgb(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)})`
      ctx.fillRect(x, 0, 1, 10)
    }
  }, [ramp])
  return <div role="img" aria-label={`${label} ${fmt(lo)} – ${fmt(hi)}`} data-ov-legend={label} style={{ margin: '4px 0' }}>
    <div style={{ fontSize: 11 }}>{label}</div>
    <canvas ref={canvas} width={180} height={10} style={{ width: 180, height: 10, display: 'block' }} />
    <div style={{ display: 'flex', justifyContent: 'space-between', width: 180, fontSize: 11 }}>
      <span>{fmt(lo)}</span><span>{fmt(hi)}</span>
    </div>
    {uniform && <div style={{ fontSize: 11, opacity: 0.8 }}>{uniform}</div>}
  </div>
}

/** Liquid DAE results coloured by Kit (Stage 4). The browser relays a
 * server-signed grant and frame numbers; Kit re-hashes the result, binds it
 * to the acknowledged scene and computes every colour. The Three.js result
 * slider drives the frame (latest wins). A new run identity or new samples
 * re-grant; while the displayed run is the live DAE experiment, its result
 * file is re-read (throttled) so both renderers follow the solve. */
export const LIVE_FOLLOW_MS = 5000
export function OvLiquidSection({ client, degraded, projectUid }: OvPostProcessPanelProps) {
  const { t } = useTranslation()
  const run = useSyncExternalStore(subscribeLiquid, liquidRunState)
  const results = run.results as Record<string, unknown> | null
  const runId = typeof results?.run_id === 'string' ? results.run_id : null
  const bound = Boolean(results?.object_bindings && results?.cell_edge_ids)
  const samples = Array.isArray(results?.time_s) ? (results!.time_s as unknown[]).length : 0
  const sample = useSyncExternalStore(subscribeResultSample, selectedResultSample)
  const [on, setOn] = useState(false)
  const [busy, setBusy] = useState(false)
  const [info, setInfo] = useState<OvLiquidInfo | null>(null)
  const [frame, setFrame] = useState<OvLiquidFrame | null>(null)
  const [note, setNote] = useState('')
  const [fixedScale, setFixedScale] = useState(() => liquidDisplayPreference(projectUid, runId)?.fixedScale ?? false)
  const mounted = useRef(true)
  const lifecycle = useRef(0)
  const scope = useRef({ client, projectUid })
  scope.current = { client, projectUid }
  const loadRef = useRef<() => Promise<void>>(async () => {})
  const paintRef = useRef<(sample: number) => Promise<void>>(async () => {})
  const isCurrent = (id?: string | null, token = lifecycle.current) => token === lifecycle.current && mounted.current && scope.current.client === client &&
    scope.current.projectUid === projectUid && (!id || (liquidRunState().results as { run_id?: unknown } | null)?.run_id === id)
  const fixedRef = useRef(fixedScale)
  fixedRef.current = fixedScale
  const inFlight = useRef(false)
  const pending = useRef<number | null>(null)
  const loadedSamples = useRef(0)
  const loadedRun = useRef<string | null>(null)
  const loading = useRef(false)
  const reloadPending = useRef(false)
  const live = useSyncExternalStore(subscribeRuntime, runtimeState)
  const liveRun = live.session?.run_id ?? null
  const following = on && liveRun !== null && liveRun === runId
  const liveFrames = following ? live.frames.length : 0
  const liveStatus = following ? live.session!.status : null
  const lastFollow = useRef(0)
  const followTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => { mounted.current = true; return () => {
    mounted.current = false; lifecycle.current += 1; pending.current = null; reloadPending.current = false
    if (followTimer.current) clearTimeout(followTimer.current)
    followTimer.current = null
  } }, [client, projectUid])

  async function paint(s: number) {
    const token = lifecycle.current
    const id = loadedRun.current
    if (!id || !isCurrent(id, token)) return
    if (inFlight.current) { pending.current = s; return }
    inFlight.current = true
    try {
      const raw = liquidRunState().results as Record<string, unknown> | null
      const ack = await client.setLiquidFrame(s, fixedRef.current && raw ? liquidGlobalDomains(raw) : null)
      if (!isCurrent(id, token)) return
      if (ack.liquidFrame) setFrame(ack.liquidFrame)
      setNote('')
    } catch (e) { if (isCurrent(id, token)) setNote(errText(e)) } finally {
      inFlight.current = false
      if (mounted.current && pending.current !== null) { const next = pending.current; pending.current = null; void paintRef.current(next) }
    }
  }
  async function load() {
    const token = lifecycle.current
    if (degraded || !isCurrent()) return
    if (loading.current) { reloadPending.current = true; return }
    loading.current = true
    try {
      const id = typeof (liquidRunState().results as { run_id?: unknown } | null)?.run_id === 'string'
        ? (liquidRunState().results as { run_id: string }).run_id : null
      if (!id || !isCurrent(id, token)) return
      // A result change resends the scene while Kit is not yet colouring
      // (~45 MB on Shinaide); a load sent behind that transfer times out.
      const idle = ovSession().sceneIdle
      if (idle) { setNote(t('ovpp.liquid_waiting_scene')); await idle(); setNote('') }
      if (!isCurrent(id, token)) return
      const granted = await requestLiquidResultGrant(id)
      if (!isCurrent(id, token)) return
      const ack = await client.loadLiquidResult(granted.grant)
      if (!isCurrent(id, token)) return
      if (!ack.liquid || ack.liquid.runId !== id) throw new Error('Kit acknowledged a different liquid run')
      setInfo(ack.liquid)
      // Another run: its slider starts at the latest sample (the results
      // panel's rule), so a stale index cannot point past a shorter run.
      if (loadedRun.current !== null && loadedRun.current !== ack.liquid.runId) setResultSample(-1)
      loadedRun.current = ack.liquid.runId
      loadedSamples.current = ack.liquid.samples
      setOvSession({ liquidActive: true })
      await paintRef.current(selectedResultSample())
      if (isCurrent(id, token)) {
        rememberLiquidDisplay(projectUid, id, { enabled: true, fixedScale: fixedRef.current }); setOn(true)
      }
    } finally {
      loading.current = false
      if (mounted.current && reloadPending.current) { reloadPending.current = false; void loadRef.current().catch(e => setNote(errText(e))) }
    }
  }
  async function toggle(next: boolean) {
    const token = lifecycle.current
    setBusy(true)
    try {
      if (next) { await load() } else {
        await client.clearLiquidColours()
        if (!isCurrent(undefined, token)) return
        rememberLiquidDisplay(projectUid, runId, { enabled: false, fixedScale: fixedRef.current })
        setOvSession({ liquidActive: false })
        setOn(false); setInfo(null); setFrame(null)
      }
    } catch (e) { if (isCurrent(undefined, token)) { setNote(errText(e)); if (next) setOvSession({ liquidActive: false }) } } finally { if (isCurrent(undefined, token)) setBusy(false) }
  }
  loadRef.current = load
  paintRef.current = paint
  useEffect(() => {
    const saved = liquidDisplayPreference(projectUid, runId)
    setOn(false); setInfo(null); setFrame(null)
    loadedRun.current = null; loadedSamples.current = 0
    setFixedScale(saved?.fixedScale ?? false); fixedRef.current = saved?.fixedScale ?? false
    if (saved?.enabled && bound && !degraded) void toggle(true)
  }, [client, projectUid, degraded]) // A new acknowledged client always requests a fresh grant.
  useEffect(() => { if (on) void paint(sample) }, [sample])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (on && runId && (runId !== loadedRun.current || samples > loadedSamples.current)) void load().catch(e => setNote(errText(e)))
  }, [runId, samples])  // eslint-disable-line react-hooks/exhaustive-deps
  // The live experiment only streams node/edge frames; the per-part rules need
  // its result file, which the run manager rewrites atomically every step.
  useEffect(() => {
    if (!following || !runId || followTimer.current) return
    const wait = Math.max(0, lastFollow.current + LIVE_FOLLOW_MS - Date.now())
    followTimer.current = setTimeout(() => {
      followTimer.current = null
      lastFollow.current = Date.now()
      void fetchLiquidResults(`run-${runId}`)
    }, wait)
  }, [following, runId, liveFrames, liveStatus])

  const group = (g: 'network' | 'chips', label: string) => {
    const d = frame?.groups[g]
    return d ? <OvLegend ramp={OV_LIQUID_RAMP} lo={d.domain[0]} hi={d.domain[1]} label={label} /> : null
  }
  return <fieldset disabled={degraded || busy}>
    <legend>{t('ovpp.liquid')}</legend>
    {!results && <p>{t('ovpp.liquid_no_results')}</p>}
    {results && !bound && <p role="note">{t('ovpp.liquid_unbound')}</p>}
    <label className="liquid-experiment-check"><input type="checkbox" aria-label={t('ovpp.liquid_toggle')}
      checked={on} disabled={!runId || !bound} onChange={e => void toggle(e.target.checked)} />{t('ovpp.liquid_toggle')}</label>
    <label className="liquid-experiment-check"><input type="checkbox" aria-label={t('ovpp.liquid_fixed_scale')}
      checked={fixedScale} disabled={!on} onChange={e => { setFixedScale(e.target.checked); fixedRef.current = e.target.checked;
        rememberLiquidDisplay(projectUid, runId, { enabled: on, fixedScale: e.target.checked }); void paint(selectedResultSample()) }} />{t('ovpp.liquid_fixed_scale')}</label>
    {liveRun && liveRun !== runId && live.session!.status !== 'failed' &&
      <button className="btn" onClick={() => void fetchLiquidResults(`run-${liveRun}`)}>{t('ovpp.liquid_follow_live', { run: liveRun.slice(0, 8) })}</button>}
    {following && <p role="status" data-ov-liquid-following={liveStatus ?? ''}>{t('ovpp.liquid_following', { status: liveStatus ?? '' })}</p>}
    {info && <p role="status" data-ov-liquid-run={info.runId}>{t('ovpp.liquid_loaded', { run: info.runId.slice(0, 8), status: info.status, samples: info.samples })}</p>}
    {info && !info.projectContentMatches && <p role="note">{t('ovpp.liquid_project_changed')}</p>}
    {frame && <p role="status" data-ov-liquid-painted={frame.painted}>{t('ovpp.liquid_frame', { time: frame.timeSeconds, painted: frame.painted,
      parts: Object.entries(frame.targets).map(([k, n]) => `${k} ${n}`).join(' · ') })}</p>}
    {frame && Object.keys(frame.unmatched).length > 0 &&
      <p role="note">{t('ovpp.liquid_unmatched', { list: Object.entries(frame.unmatched).map(([k, n]) => `${k} ${n}`).join(' · ') })}</p>}
    {group('network', t('ovpp.liquid_network'))}
    {group('chips', t('ovpp.liquid_chips'))}
    <p className="liquid-experiment-note">{t('ovpp.liquid_hint')}</p>
    {note && <p role="status">{note}</p>}
  </fieldset>
}

/** What the OV run list depends on: the case directory and its solved air runs. */
export function ovRunsKey(info: Pick<ServerInfo, 'caseDir'> | null, status: Pick<CaseStatus, 'logRuns'> | null): string {
  return JSON.stringify([info?.caseDir ?? null, (status?.logRuns ?? []).map(r => r.name)])
}

export function OvPostProcessPanel({ client, degraded, projectUid, initiallyOpen = true }: OvPostProcessPanelProps & {initiallyOpen?: boolean}) {
  const { t } = useTranslation()
  const [runs, setRuns] = useState<OvRun[] | null>(null)
  const [runId, setRunId] = useState('')
  const [selectedTime, setSelectedTime] = useState<number | null>(null)
  const selectedTimeRef = useRef<number | null>(null)
  const anchor = useRef(store.playheadTime())
  const playhead = useSyncExternalStore(store.subscribePlayhead, store.playheadTime)
  const [dataset, setDataset] = useState<OvDatasetInfo | null>(null)
  const [loadingData, setLoadingData] = useState(false)
  const [volume, setVolume] = useState<OvVolumeState>({ field: 'off', visible: false })
  const [volumeBusy, setVolumeBusy] = useState(false)
  const [volumeRange, setVolumeRange] = useState<[number, number] | null>(null)
  const [slice, setSlice] = useState<OvSliceParams>({
    visible: false, origin: [0, 1, 0], normal: [0, 1, 0], radius: 2, field: 'T', domain: null,
  })
  const [sliceResult, setSliceResult] = useState<OvSliceResult | null>(null)
  const [adopted, setAdopted] = useState(false)
  const [normalKey, setNormalKey] = useState<'y' | 'yneg' | 'x' | 'z'>('y')
  const [streamOn, setStreamOn] = useState(false)
  const [streamField, setStreamField] = useState<'T' | 'U'>('T')
  const [seed, setSeed] = useState({ center: [0, 1, 0] as Vec3, radius: 1, nSeeds: 27 })
  const [note, setNote] = useState('')
  const [seedVisible, setSeedVisible] = useState(false)
  const sliceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mounted = useRef(true)
  const lifetime = useRef(0)
  const scope = useRef({ client, projectUid, runsKey: '' })
  const listedKey = useRef('')
  const activeDataset = useRef<string | null>(null)
  const display = useRef<AirDisplayPreference>({ volume: { field: 'off', domain: null }, slice, normalKey,
    streamOn, streamField, seed, seedVisible })
  display.current = { volume: { field: volume.field, domain: volume.domain ?? null }, slice, normalKey,
    streamOn, streamField, seed, seedVisible }
  type DatasetJob = { id: string; time: number | null; restore: AirDisplayPreference | null; token: number; serial: number }
  const serial = useRef(0)
  const requestedRun = useRef<string | null>(null)
  const loading = useRef(false)
  const pendingData = useRef<DatasetJob | null>(null)
  const loadRef = useRef<(job: DatasetJob) => Promise<void>>(async () => {})
  const restored = useRef('')
  type FireName = Parameters<OvCommandClient['fireCommand']>[0]
  type FireJob = { name: FireName; args: object; patch?: Partial<AirDisplayPreference>; token: number; id: string | null; generation: number }
  const fireJobs = useRef(new Map<FireName, FireJob>())
  const firing = useRef(false)
  const displayMutations = useRef(new Set<Promise<unknown>>())
  function trackDisplay<T>(promise: Promise<T>): Promise<T> {
    displayMutations.current.add(promise)
    void promise.then(() => displayMutations.current.delete(promise), () => displayMutations.current.delete(promise))
    return promise
  }
  const drainFireRef = useRef<() => Promise<void>>(async () => {})
  const isCurrent = (token = lifetime.current) => mounted.current && token === lifetime.current &&
    scope.current.client === client && scope.current.projectUid === projectUid && scope.current.runsKey === runsKey
  const saveDisplay = (patch: Partial<AirDisplayPreference> = {}) => {
    if (isCurrent() && activeDataset.current) rememberAirDisplay(projectUid, activeDataset.current, { ...display.current, ...patch })
  }

  // Re-list whenever the case or its solved runs change: a project switch or a
  // solve finishing while OV is open used to leave the list empty or stale.
  const runsKey = useSyncExternalStore(subscribeSession, () => ovRunsKey(getServerInfo(), getCaseStatus()))
  scope.current = { client, projectUid, runsKey }
  useEffect(() => {
    mounted.current = true
    activeDataset.current = null; restored.current = ''
    setDataset(null); setVolume({ field: 'off', visible: false }); setVolumeRange(null)
    setSlice({ visible: false, origin: [0, 1, 0], normal: [0, 1, 0], radius: 2, field: 'T', domain: null })
    setSliceResult(null); setAdopted(false); setNormalKey('y')
    setStreamOn(false); setStreamField('T'); setSeed({ center: [0, 1, 0], radius: 1, nSeeds: 27 }); setSeedVisible(false)
    return () => {
      mounted.current = false; lifetime.current += 1; serial.current += 1; pendingData.current = null; fireJobs.current.clear()
      if (sliceTimer.current) clearTimeout(sliceTimer.current)
      sliceTimer.current = null
    }
  }, [client, projectUid, runsKey])
  useEffect(() => {
    let live = true
    listOvRuns().then(list => {
      if (!live) return
      listedKey.current = runsKey
      setRuns(list)
      setRunId(id => {
        const selected = selectedAirDisplayRun(projectUid)
        return selected && list.some(r => r.runId === selected) ? selected : list.some(r => r.runId === id) ? id : list[0]?.runId ?? ''
      })
    }).catch(e => { if (live) { setRuns([]); setNote(errText(e)) } })
    return () => { live = false }
  }, [runsKey])

  function adoptSliceAck(s: NonNullable<Awaited<ReturnType<OvCommandClient['getSliceState']>>['slice']>, persist = true) {
    const prev = display.current.slice
    const next: OvSliceParams = {
      visible: typeof s.visible === 'boolean' ? s.visible : prev.visible,
      origin: Array.isArray(s.origin) && s.origin.length === 3 ? s.origin.map(round3) as Vec3 : prev.origin,
      normal: Array.isArray(s.normal) && s.normal.length === 3 ? s.normal as Vec3 : prev.normal,
      radius: typeof s.radius === 'number' ? s.radius : prev.radius,
      field: s.field === 'U' || s.field === 'Umag' ? 'Umag' : 'T',
      domain: Array.isArray(s.domain) ? s.domain as [number, number] : null,
    }
    setSlice(next)
    const nk = NORMALS.find(n => n.value.every((v, i) => Math.abs(v - (s.normal?.[i] ?? NaN)) < 1e-9))
    if (nk) setNormalKey(nk.key)
    if (persist) saveDisplay({ slice: next, normalKey: nk?.key ?? display.current.normalKey })
    return next
  }

  // The slice lives in Kit; a viewport drag moves it without the panel knowing.
  // Reading back makes Kit adopt the drag, re-cut there, and report it.
  async function readBackSlice() {
    const token = lifetime.current, generation = serial.current
    try {
      const idle = ovSession().sceneIdle
      if (idle) await idle()
      if (!isCurrent(token) || generation !== serial.current) return
      const ack = await trackDisplay(client.getSliceState())
      if (!isCurrent(token) || generation !== serial.current) return
      if (ack.slice) adoptSliceAck(ack.slice)
      setAdopted(Boolean(ack.adopted))
      if (ack.sliceResult !== undefined) setSliceResult(ack.sliceResult ?? null)
      setNote('')
    } catch (e) { if (isCurrent(token) && generation === serial.current) setNote(errText(e)) }
  }
  useEffect(() => { if (!selectedAirDisplayRun(projectUid)) void readBackSlice() }, [client, projectUid])  // eslint-disable-line react-hooks/exhaustive-deps

  async function performDataset(job: DatasetJob) {
    loading.current = true
    setLoadingData(true); setVolumeBusy(false); setNote('')
    const current = () => isCurrent(job.token) && job.serial === serial.current
    try {
      const idle = ovSession().sceneIdle
      if (idle) await idle()
      if (!current()) return
      const granted = await requestOvDatasetGrant(job.id, undefined, job.time ?? undefined)
      if (!current()) return
      // Old volume/slice/fire mutations must finish before a new import.
      // Generation checks alone would hide their ACK, not stop Kit mutation.
      await Promise.allSettled([...displayMutations.current])
      if (!current()) return
      const ack = await client.loadResultDataset(granted.grant)
      if (!current()) return
      if (!ack.dataset || ack.dataset.runId !== job.id) throw new Error('Kit acknowledged a different air run')
      if (ack.dataset.time !== granted.time || (job.time !== null && Number(granted.time) !== job.time))
        throw new Error('Kit acknowledged a different air frame')
      setDataset(ack.dataset); activeDataset.current = job.id
      const b = ack.dataset.bounds
      let nextSlice: OvSliceParams = { visible: false, origin: [0, 1, 0], normal: [0, 1, 0], radius: 2, field: 'T', domain: null }
      let nextSeed = { center: [0, 1, 0] as Vec3, radius: 1, nSeeds: 27 }
      if (b && !job.restore) {
        const center = [(b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2].map(round3) as Vec3
        const ext = [b[1] - b[0], b[3] - b[2], b[5] - b[4]]
        nextSlice = { ...nextSlice, origin: center, radius: round3(Math.max(...ext) * .6) || nextSlice.radius }
        nextSeed = { ...nextSeed, center, radius: round3(Math.min(...ext) * .25) || nextSeed.radius }
      }
      if (!job.restore) {
        // load_dataset clears the volume, streamlines and seed; the slice
        // survives in Kit, so explicitly clear it when choosing a new run.
        const sliceAck = await client.setSlice(nextSlice)
        if (!current()) return
        if (sliceAck.slice) nextSlice = adoptSliceAck(sliceAck.slice, false)
        setSliceResult(sliceAck.sliceResult ?? null)
        setVolume({ field: 'off', visible: false }); setVolumeRange(null)
        setSlice(nextSlice); setSeed(nextSeed); setStreamOn(false); setStreamField('T'); setSeedVisible(false); setNormalKey('y')
        rememberAirDisplay(projectUid, job.id, { volume: { field: 'off', domain: null }, slice: nextSlice, seed: nextSeed,
          normalKey: 'y', streamOn: false, streamField: 'T', seedVisible: false })
      } else {
        const saved = job.restore
        const volumeAck = await client.setVolumeDisplay(saved.volume.field, saved.volume.domain)
        if (!current()) return
        if (!volumeAck.volume) throw new Error('Kit acknowledged without volume display details')
        setVolume(volumeAck.volume); setVolumeRange(volumeAck.volume.domain ?? null)
        const sliceAck = await client.setSlice(saved.slice)
        if (!current()) return
        setSlice(saved.slice); setNormalKey(saved.normalKey)
        const restoredSlice = sliceAck.slice ? adoptSliceAck(sliceAck.slice, false) : saved.slice
        setSliceResult(sliceAck.sliceResult ?? null)
        await client.fireCommand('setStreamlines', { enabled: saved.streamOn, field: saved.streamField,
          seedCenter: saved.seed.center, seedRadius: saved.seed.radius, nSeeds: saved.seed.nSeeds, maxSteps: 500, direction: 'both' })
        if (!current()) return
        setStreamOn(saved.streamOn); setStreamField(saved.streamField); setSeed(saved.seed)
        if (saved.seedVisible) {
          await client.fireCommand('setSeedSphere', { visible: true, center: saved.seed.center, radius: saved.seed.radius, nSeeds: saved.seed.nSeeds })
          if (!current()) return
        }
        setSeedVisible(saved.seedVisible)
        rememberAirDisplay(projectUid, job.id, { ...saved, slice: restoredSlice, volume: { field: volumeAck.volume.field, domain: volumeAck.volume.domain ?? null } })
      }
    } catch (e) { if (current()) setNote(errText(e)) }
    finally {
      loading.current = false
      const next = pendingData.current; pendingData.current = null
      if (mounted.current && next) void loadRef.current(next)
      else if (mounted.current) setLoadingData(false)
    }
  }
  loadRef.current = performDataset
  function loadDataset(id = runId, restore: AirDisplayPreference | null = airDisplayPreference(projectUid, id), time = selectedTimeRef.current) {
    if (!id || degraded || !isCurrent()) return
    requestedRun.current = id
    const job = { id, time, restore, token: lifetime.current, serial: ++serial.current }
    fireJobs.current.clear()
    if (sliceTimer.current) clearTimeout(sliceTimer.current)
    sliceTimer.current = null
    rememberAirFrame(projectUid, id, { time, anchor: anchor.current })
    if (loading.current) pendingData.current = job
    else void performDataset(job)
  }
  function initialFrame(id: string) {
    const run = runs?.find(r => r.runId === id)
    const times = run?.timesteps ?? []
    const saved = airFramePreference(projectUid, id)
    if (saved && (saved.time === null || times.includes(saved.time))) {
      if (saved.anchor === store.playheadTime() || !run?.transient || !projectIsTransient()) return saved.time
    }
    // A Three result already has an accepted project/run/time context. Transfer
    // only its selection; dataset grants do not replace that independent gate.
    const context = currentResultLayerContext()
    const selection = currentLiveAirResultSelection()
    if (!saved && context?.source.projectUid === projectUid && selection?.session.runId === id &&
        selection.session.timeValue !== null && times.includes(selection.session.timeValue)) return selection.session.timeValue
    if (run?.transient && projectIsTransient() && store.playheadTime() > 0) {
      return times[nearestIndex(times, store.playheadTime())] ?? null
    }
    return null
  }
  useEffect(() => {
    const time = initialFrame(runId)
    selectedTimeRef.current = time; setSelectedTime(time); anchor.current = store.playheadTime()
  }, [runId, runs])
  function chooseFrame(time: number | null) {
    if (time !== null && !runs?.find(r => r.runId === runId)?.timesteps?.includes(time)) return
    selectedTimeRef.current = time; setSelectedTime(time)
    if (time !== null && projectIsTransient()) store.setPlayhead(time)
    anchor.current = store.playheadTime()
    rememberAirFrame(projectUid, runId, { time, anchor: anchor.current })
    if (activeDataset.current === runId || (loading.current && requestedRun.current === runId))
      loadDataset(runId, display.current, time)
  }
  useEffect(() => {
    const run = runs?.find(r => r.runId === runId)
    if (!run?.transient || !projectIsTransient() || playhead === anchor.current) return
    const time = run.timesteps?.[nearestIndex(run.timesteps, playhead)]
    if (time === undefined) return
    const handle = setTimeout(() => {
      anchor.current = playhead
      selectedTimeRef.current = time; setSelectedTime(time)
      rememberAirFrame(projectUid, runId, { time, anchor: playhead })
      if (activeDataset.current === runId || (loading.current && requestedRun.current === runId))
        loadDataset(runId, display.current, time)
    }, 250)
    return () => clearTimeout(handle)
  }, [playhead, runId, runs])
  useEffect(() => {
    if (degraded || !runs || listedKey.current !== runsKey) return
    const selected = selectedAirDisplayRun(projectUid)
    const saved = airDisplayPreference(projectUid, selected)
    if (!selected || !saved || !runs.some(r => r.runId === selected)) return
    const key = JSON.stringify([lifetime.current, projectUid, runsKey, selected])
    if (restored.current === key) return
    restored.current = key
    const time = initialFrame(selected)
    selectedTimeRef.current = time; setSelectedTime(time); anchor.current = store.playheadTime()
    setRunId(selected); loadDataset(selected, saved, time)
  }, [client, projectUid, degraded, runsKey, runs]) // Restore ordinary choices only after a fresh scoped grant/ACK.

  async function showVolume(field: 'off' | 'T' | 'U', domain: [number, number] | null) {
    const token = lifetime.current, id = activeDataset.current, generation = serial.current
    setVolumeBusy(true)
    setNote('')
    try {
      const ack = await trackDisplay(client.setVolumeDisplay(field, domain))
      if (!isCurrent(token) || (id !== activeDataset.current || generation !== serial.current)) return
      if (ack.volume) {
        setVolume(ack.volume)
        if (ack.volume.domain) setVolumeRange(ack.volume.domain)
        saveDisplay({ volume: { field: ack.volume.field, domain: ack.volume.domain ?? null } })
      }
    } catch (e) { if (isCurrent(token) && id === activeDataset.current && generation === serial.current) setNote(errText(e)) } finally { if (isCurrent(token) && generation === serial.current) setVolumeBusy(false) }
  }

  function sendSlice(next: OvSliceParams) {
    setSlice(next)
    setAdopted(false)
    if (sliceTimer.current) clearTimeout(sliceTimer.current)
    // Drag-grade edits from the panel should not spam the ack channel.
    const token = lifetime.current, id = activeDataset.current, generation = serial.current
    sliceTimer.current = setTimeout(() => {
      if (!isCurrent(token) || (id !== activeDataset.current || generation !== serial.current)) return
      void trackDisplay(client.setSlice(next)).then(ack => {
        if (!isCurrent(token) || (id !== activeDataset.current || generation !== serial.current)) return
        saveDisplay({ slice: next })
        setNote('')
        if (ack.sliceResult !== undefined) setSliceResult(ack.sliceResult ?? null)
      }).catch(e => { if (isCurrent(token) && id === activeDataset.current && generation === serial.current) setNote(errText(e)) })
    }, 200)
  }
  const patchSlice = (p: Partial<OvSliceParams>) => sendSlice({ ...slice, ...p })

  async function drainFire() {
    if (firing.current || !mounted.current) return
    const job = fireJobs.current.values().next().value as FireJob | undefined
    if (!job) return
    fireJobs.current.delete(job.name)
    if (!isCurrent(job.token) || job.id !== activeDataset.current || job.generation !== serial.current) { void drainFireRef.current(); return }
    firing.current = true
    try {
      await trackDisplay(client.fireCommand(job.name, job.args))
      if (isCurrent(job.token) && job.id === activeDataset.current && job.generation === serial.current && !fireJobs.current.has(job.name) && job.patch) saveDisplay(job.patch)
    } catch (e) { if (isCurrent(job.token) && job.generation === serial.current) setNote(errText(e)) }
    finally { firing.current = false; if (mounted.current) void drainFireRef.current() }
  }
  drainFireRef.current = drainFire
  const fire = (name: FireName, args: object, patch?: Partial<AirDisplayPreference>) => {
    fireJobs.current.set(name, { name, args, patch, token: lifetime.current, id: activeDataset.current, generation: serial.current })
    void drainFire()
  }

  const uniformNote = (lo: number | null | undefined, hi: number | null | undefined) =>
    lo != null && hi != null && Math.abs(hi - lo) <= 1e-9 * Math.max(1, Math.abs(lo)) ? t('ovpp.legend_uniform', { value: fmt(lo) }) : undefined
  const volumeStats = volume.field === 'T' ? dataset?.fields.T : volume.field === 'U' ? dataset?.fields.Umag : undefined
  const sliceUnit = slice.field === 'T' ? 'T (K)' : '|U| (m/s)'
  const sliceLo = slice.domain?.[0] ?? sliceResult?.min
  const sliceHi = slice.domain?.[1] ?? sliceResult?.max

  return <details open={initiallyOpen} style={{ background: '#20252d', padding: 8, marginTop: 6, pointerEvents: 'auto', fontSize: 12, maxHeight: '70vh', overflowY: 'auto' }}>
    <summary style={{ cursor: 'pointer' }}>{t('ovpp.title')}{degraded ? ` · ${t('ovpp.degraded_suffix')}` : ''}</summary>
    {degraded && <p role="note">{t('ovpp.degraded_note')}</p>}
    <OvLiquidSection key={projectUid ?? 'anonymous'} client={client} degraded={degraded} projectUid={projectUid} />
    <fieldset disabled={degraded}>
      <legend>{t('ovpp.dataset')}</legend>
      {runs && runs.length === 0 && <p>{t('ovpp.no_runs')}</p>}
      {runs && runs.length > 0 && <label>{t('ovpp.dataset_run')} <select aria-label={t('ovpp.dataset_run')} value={runId}
        onChange={e => setRunId(e.target.value)}>
        {runs.map(r => <option key={r.runId} value={r.runId}>{r.name}</option>)}
      </select></label>}
      {runs?.find(r => r.runId === runId)?.timesteps?.length ? <label>{t('pp.time_label')} <select
        id="ov-air-time" aria-label={t('pp.time_label')} value={selectedTime ?? ''}
        onChange={e => chooseFrame(e.target.value === '' ? null : Number(e.target.value))}>
        <option value="">{t('pp.latest_complete_time')}</option>
        {runs.find(r => r.runId === runId)!.timesteps!.map(time => <option key={time} value={time}>{time}</option>)}
      </select></label> : null}
      <button className="btn" disabled={!runId || loadingData} onClick={() => void loadDataset()}>
        {loadingData ? t('ovpp.dataset_loading') : t('ovpp.dataset_load')}</button>
      {dataset && <p role="status" data-ov-dataset={dataset.run}>{t('ovpp.dataset_loaded', { run: dataset.run, time: dataset.time })}</p>}
      <p className="liquid-experiment-note">{t('ovpp.dataset_hint')}</p>
    </fieldset>
    <fieldset disabled={degraded || !dataset || volumeBusy || loadingData}>
      <legend>{t('ovpp.volume')}</legend>
      {(['off', 'T', 'U'] as const).map(f =>
        <label key={f}><input type="radio" name="ov-volume" checked={volume.field === f}
          onChange={() => void showVolume(f, null)} />{f === 'off' ? t('ovpp.off') : f === 'T' ? t('ovpp.temp') : t('ovpp.velocity')}</label>)}
      {volumeBusy && <span> {t('ovpp.working')}</span>}
      {volume.field !== 'off' && volume.domain && <>
        <OvLegend ramp={OV_VOLUME_RAMP} lo={volume.domain[0]} hi={volume.domain[1]}
          label={volume.field === 'T' ? 'T (K)' : '|U| (m/s)'}
          uniform={volume.degenerate ? uniformNote(volumeStats?.p1, volumeStats?.p99) : undefined} />
        <p className="liquid-experiment-note">{t('ovpp.volume_source', { source: volume.source ?? '' })}</p>
        {volume.composited === false && <p role="note">{t('ovpp.volume_not_composited')}</p>}
        <label>{t('ovpp.range_min')} <input type="number" aria-label={t('ovpp.range_min')} step="any"
          value={volumeRange?.[0] ?? ''} onChange={e => setVolumeRange(r => [e.target.valueAsNumber, r?.[1] ?? NaN])} /></label>
        <label>{t('ovpp.range_max')} <input type="number" aria-label={t('ovpp.range_max')} step="any"
          value={volumeRange?.[1] ?? ''} onChange={e => setVolumeRange(r => [r?.[0] ?? NaN, e.target.valueAsNumber])} /></label>
        <button className="btn" disabled={!volumeRange || !(volumeRange[0] < volumeRange[1])}
          onClick={() => volumeRange && void showVolume(volume.field, volumeRange)}>{t('ovpp.apply_range')}</button>
      </>}
    </fieldset>
    <fieldset disabled={degraded || loadingData}>
      <legend>{t('ovpp.slice')}</legend>
      <label className="liquid-experiment-check"><input type="checkbox" aria-label={t('ovpp.slice_visible')}
        checked={slice.visible} onChange={e => patchSlice({ visible: e.target.checked })} />{t('ovpp.slice_visible')}</label>
      <label>{t('ovpp.normal')} <select aria-label={t('ovpp.normal')} value={normalKey}
        onChange={e => {
          const nk = NORMALS.find(n => n.key === e.target.value)!
          setNormalKey(nk.key)
          patchSlice({ normal: nk.value })
        }}>
        {NORMALS.map(n => <option key={n.key} value={n.key}>{n.label}</option>)}
      </select></label>
      {([0, 1, 2] as const).map(i =>
        <label key={i}>{['X', 'Y', 'Z'][i]} <input type="number" aria-label={`${t('ovpp.origin')} ${['X', 'Y', 'Z'][i]}`} step="0.05"
          value={slice.origin[i]} onChange={e => {
            const origin = [...slice.origin] as Vec3
            origin[i] = e.target.valueAsNumber
            patchSlice({ origin })
          }} /></label>)}
      <label>{t('ovpp.radius')} <input type="number" aria-label={t('ovpp.radius')} step="0.05" min="0.01"
        value={slice.radius} onChange={e => patchSlice({ radius: e.target.valueAsNumber })} /></label>
      <label>{t('ovpp.field')} <select aria-label={t('ovpp.field')} value={slice.field}
        onChange={e => patchSlice({ field: e.target.value as OvSliceParams['field'] })}>
        <option value="T">T</option><option value="Umag">|U|</option>
      </select></label>
      <button className="btn" onClick={() => void readBackSlice()}>{t('ovpp.read_drag')}</button>
      {adopted && <p role="status">{t('ovpp.slice_adopted')}</p>}
      {sliceResult && <p role="status" data-ov-slice-tris={sliceResult.tris}>
        {sliceResult.tris > 0 ? t('ovpp.slice_result', { tris: sliceResult.tris }) : t('ovpp.slice_empty', { reason: sliceResult.reason ?? '' })}</p>}
      {slice.visible && sliceResult && sliceResult.tris > 0 && sliceLo != null && sliceHi != null &&
        <OvLegend ramp={OV_SLICE_RAMP} lo={sliceLo} hi={sliceHi} label={sliceUnit} uniform={uniformNote(sliceLo, sliceHi)} />}
      <p className="liquid-experiment-note">{t('ovpp.slice_hint')}</p>
    </fieldset>
    <fieldset disabled={degraded || !dataset || loadingData}>
      <legend>{t('ovpp.streamlines')}</legend>
      <label className="liquid-experiment-check"><input type="checkbox" aria-label={t('ovpp.stream_on')}
        checked={streamOn} onChange={e => {
          setStreamOn(e.target.checked)
          fire('setStreamlines', { enabled: e.target.checked, field: streamField,
            seedCenter: seed.center, seedRadius: seed.radius, nSeeds: seed.nSeeds, maxSteps: 500, direction: 'both' },
            { streamOn: e.target.checked, streamField, seed })
        }} />{t('ovpp.stream_on')}</label>
      <label>{t('ovpp.field')} <select aria-label={t('ovpp.stream_field')} value={streamField}
        onChange={e => setStreamField(e.target.value as 'T' | 'U')}>
        <option value="T">T</option><option value="U">|U|</option>
      </select></label>
      {([0, 1, 2] as const).map(i =>
        <label key={i}>{['X', 'Y', 'Z'][i]} <input type="number" aria-label={`${t('ovpp.seed_center')} ${['X', 'Y', 'Z'][i]}`} step="0.05"
          value={seed.center[i]} onChange={e => {
            const center = [...seed.center] as Vec3
            center[i] = e.target.valueAsNumber
            setSeed(s => ({ ...s, center }))
          }} /></label>)}
      <label>{t('ovpp.seed_radius')} <input type="number" aria-label={t('ovpp.seed_radius')} step="0.05" min="0.01"
        value={seed.radius} onChange={e => setSeed(s => ({ ...s, radius: e.target.valueAsNumber }))} /></label>
      <label>{t('ovpp.seed_count')} <input type="number" aria-label={t('ovpp.seed_count')} step="1" min="1"
        value={seed.nSeeds} onChange={e => setSeed(s => ({ ...s, nSeeds: e.target.valueAsNumber }))} /></label>
      <button className="btn" onClick={() => { setSeedVisible(true); fire('setSeedSphere', { visible: true,
        center: seed.center, radius: seed.radius, nSeeds: seed.nSeeds }, { seedVisible: true, seed }) }}>{t('ovpp.show_seed')}</button>
      <button className="btn" onClick={() => fire('syncFromViewport', {})}>{t('ovpp.retrace')}</button>
      <p className="liquid-experiment-note">{t('ovpp.stream_hint')}</p>
    </fieldset>
    {note && <p role="status">{note}</p>}
  </details>
}
