import {captureDiagnosticContext,diagnosticRequest,validateDiagnosticReply,type DiagnosticContext, type DiagnosticReply} from './ovDisplayDiagnostics'
import type { RenderViewState, SceneSnapshot } from './sceneSnapshot'
import { validateSceneSnapshot } from './validateScene'
import type { RenderOverlayPacket } from './meshPreviewOverlay'
import { validateRenderOverlay } from './validateOverlay'
import { assertResultLayerCompatibility, type ResultLayerSet, type ResultSource } from './resultLayers'

export interface RenderAck { diagnostics?:Record<string,unknown>; packetSha256?:string; source?:ResultSource;timeSeconds?:number|null;displayGeneration?:number;requestId: string; ok: boolean; error?: string; view?: RenderViewState; primitives?: number; received?: number; revision?: number; layers?: number; sceneRevision?: number; projectHash?: string;
  /** Common view decoration status; never a numeric/source authorisation. */
  gridDisplay?: {phase:'pending'|'unsupported'|'rejected'|'rendered'|'hidden';reason?:string;viewRevision?:number}
  /** setSlice/getSliceState reply: the slice state Kit actually holds. */
  slice?: { visible?: boolean; origin?: number[]; normal?: number[]; radius?: number; field?: string; domain?: number[] | null }
  /** setSlice/getSliceState: what the cut produced (acked after authoring). */
  sliceResult?: OvSliceResult | null
  /** getSliceState: true when Kit adopted a viewport drag of the preview disc. */
  adopted?: boolean
  /** loadResultDataset reply: the dataset Kit ingested and its field statistics. */
  dataset?: OvDatasetInfo
  /** setVolumeDisplay reply: the colour domain IndeX was actually given. */
  volume?: OvVolumeState
  /** loadLiquidResult reply: the verified run Kit now colours from. */
  liquid?: OvLiquidInfo
  /** setLiquidFrame reply: what Kit painted for that sample. */
  liquidFrame?: OvLiquidFrame
  /** clearLiquidColours reply: primitives restored. */
  cleared?: number
  /** getViewState reply: the request id of the last scene Kit authored (scene-ack recovery). */
  sceneRequestId?: string | null
  /** A scene ack reconstructed from Kit's reported state after the real ack was lost. */
  recovered?: boolean }

export interface OvLiquidInfo {
  runId: string; solver: string; status: string; samples: number; times: [number, number]
  targets: Record<string, number>; projectUid: string
  /** false: the project changed after this run was solved (display still allowed, labelled). */
  projectContentMatches: boolean
}
export interface OvLiquidGroup { domain: [number, number]; source: string; count: number }
export interface OvLiquidFrame {
  sample: number; timeSeconds: number
  groups: Partial<Record<'network' | 'chips', OvLiquidGroup>>
  targets: Record<string, number>; painted: number; unmatched: Record<string, number>
}

export interface OvFieldStats { min: number; max: number; p1: number; p99: number }
export interface OvDatasetInfo {
  run: string; runId: string; time: string
  fields: Partial<Record<'T' | 'Umag', OvFieldStats>>
  ranges: Record<string, [number, number]>
  bounds?: [number, number, number, number, number, number] | null
  hasT: boolean; hasU: boolean
}
export interface OvVolumeState {
  field: 'off' | 'T' | 'U'; visible: boolean
  /** IndeX switched nvindex:composite on, i.e. the volume is actually drawn. */
  composited?: boolean
  source?: string; degenerate?: boolean; domain?: [number, number] | null
}
export interface OvSliceResult {
  tris: number; field?: string; found?: boolean; min?: number | null; max?: number | null
  cached?: boolean; reason?: string | null
}
/** A server-signed display grant (`/api/ov/result-dataset`), relayed verbatim. */
export interface OvDisplayGrant { payload: Record<string, unknown>; signature: string }
/** Kit authors one USD material per primitive and then compiles them for its
 * first frame, with its main loop blocked throughout: Base604 (10k primitives)
 * took ~3.5 min on a loaded 40901. Budget the scene ack by size, never below
 * the base. */
export function sceneAckBudgetMs(baseMs: number, primitives: number): number {
  return Math.max(baseMs, 60000 + 30 * primitives)
}
export type SendCommand = (payload: { command_name: string; message: string }) => Promise<unknown>
/** sending: `received` of `chunks` confirmed by Kit; building: all sent, Kit authoring USD. */
export interface SceneProgress { phase: 'sending' | 'building'; received: number; chunks: number }

export interface OvSliceParams {
  visible: boolean
  origin: [number, number, number]
  normal: [number, number, number]
  radius: number
  field: 'T' | 'U' | 'Umag'
  domain: [number, number] | null
}

/** Degraded-display eligibility: the acknowledgement channel timed out BUT the
 * WebRTC video is provably live (frames decoding). OV is a display-only
 * renderer by product ruling, so an unconfirmed re-render may stay on screen
 * behind an explicit "unverified" banner; anything else (no video, transport
 * error, rejection) is a hard failure that returns the user to Three.js.
 * Pure so the policy is unit-testable. */
export function ovAckTimeoutDegraded(errorMessage: string, decodedVideoFrames: number): boolean {
  return /acknowledgement timed out|command submission timed out/.test(errorMessage) && decodedVideoFrames > 30
}

// ACKs contain view state/diagnostics, never scene meshes or embedded textures.
// An official 16,128-character chunk can occupy nearly 64 KiB in UTF-8.
const MAX_ACK_PART_BYTES = 64 * 1024
const MAX_ACK_BYTES = 1024 * 1024
const MAX_BUFFERED_ACK_BYTES = 2 * MAX_ACK_BYTES
const MAX_ACK_PARTS = 128
const MAX_ACK_GROUPS = 8
const MAX_TRANSPORT_ID_LENGTH = 128
const utf8 = new TextEncoder()

interface PendingAck {
  resolve: (ack: RenderAck) => void
  reject: (e: Error) => void
  /** Resolve with the ack even when ok=false (readiness probes). */
  settleAny?: boolean
  diagnostics?: DiagnosticContext
  timer: ReturnType<typeof setTimeout>
  sequence: number
}
interface AckGroup {
  count: number
  parts: Map<number, string>
  bytes: number
  invalid: boolean
  requestCutoff: number
  timer: ReturnType<typeof setTimeout>
}

/** LAN HTTP lacks randomUUID, while getRandomValues remains cryptographically secure. */
function requestUuid(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function boundedBytes(value: string, limit: number): number | null {
  // Reject before allocating encoded bytes for an obviously oversized string.
  if (value.length > limit) return null
  const bytes = utf8.encode(value).byteLength
  return bytes <= limit ? bytes : null
}

/** Transport delivery is not scene readiness. A Kit ACK is required for every mutation. */
export class OvCommandClient {
  private pending = new Map<string, PendingAck>()
  private ackGroups = new Map<string, AckGroup>()
  private ackBytes = 0
  private requestSequence = 0
  private closed = false
  private diagnosticInFlight = false
  private editEventHandler: ((event: unknown) => void) | null = null
  private sceneProgressHandler: ((progress: SceneProgress) => void) | null = null
  private transferQueue: Promise<void> = Promise.resolve()
  // A Shinaide scene is thousands of chunks. Kit processes its command queue
  // after transport has accepted the messages, so its scene ACK needs a longer
  // budget than a small camera/selection command.
  // The livestream plugin can drop Kit's outbound messages when its send path
  // is busy (NVST_R_BUSY bursts, measured on 40901 2026-09-30): a lost
  // progress ack must not fail a ~2 min transfer, and a lost scene ack is
  // recovered from Kit's idempotent view state (sceneRequestId).
  constructor(private send: SendCommand, private timeoutMs = 45000,
    private chunkSize = 12000, private sceneTimeoutMs = 240000,
    private progressBatchSize = 64, private progressAttempts = 4,
    private sceneRecoverAfterMs = 15000, private sceneRecoverEveryMs = 5000) {}

  receive(event: unknown) {
    if (this.closed) return
    let value = event
    // Accept the existing string/message wrappers without recursive or cyclic
    // input being able to overflow the stack. Official SDK app messages arrive
    // as the flat object after its JSON decode.
    for (let depth = 0; depth < 4; depth++) {
      if (typeof value === 'string') {
        if (boundedBytes(value, MAX_ACK_BYTES + 2048) === null) return
        try { value = JSON.parse(value) } catch { return }
      }
      if (!record(value)) return
      if (value.event_type === undefined && 'message' in value) {
        value = value.message
        continue
      }
      if (value.event_type === 'dcdtuni:ovEditEvent') {
        if (this.editEventHandler && record(value.payload)) {
          try {
            if (boundedBytes(JSON.stringify(value.payload), MAX_ACK_PART_BYTES) !== null) {
              this.editEventHandler(value.payload)
            }
          } catch { /* malformed events never interrupt the render stream */ }
        }
        return
      }
      if (value.event_type !== 'dcdtuni:renderAck' || !this.pending.size) return
      if ('part' in value || 'numParts' in value) this.receiveAckPart(value)
      else this.acceptAck(value.payload)
      return
    }
  }

  onEditEvent(handler: ((event: unknown) => void) | null): void {
    this.editEventHandler = handler
  }

  /** Scene transfers take tens of seconds; report confirmed chunks, then the
   * Kit-side build, so the viewport shows progress instead of a frozen line. */
  onSceneProgress(handler: ((progress: SceneProgress) => void) | null): void {
    this.sceneProgressHandler = handler
  }
  private sceneProgress(progress: SceneProgress) {
    try { this.sceneProgressHandler?.(progress) } catch { /* display only */ }
  }

  private acceptAck(value: unknown, requestCutoff = Infinity, alreadyBounded = false) {
    if (!record(value) || typeof value.requestId !== 'string' || !value.requestId || value.requestId.length > 256 || typeof value.ok !== 'boolean') return
    if (value.error !== undefined && typeof value.error !== 'string') return
    if (!alreadyBounded) {
      try { if (boundedBytes(JSON.stringify(value), MAX_ACK_BYTES) === null) return } catch { return }
    }
    const requestId = value.requestId
    const p = this.pending.get(value.requestId)
    if (!p || p.sequence > requestCutoff) return
    if (p.diagnostics) {
      try {
        if(value.ok===false) {
          if(Object.keys(value).some(k=>!['requestId','ok','error'].includes(k))||
             typeof value.error!=='string'||value.error.length>128)throw new Error('Invalid bounded display refusal')
        }else value=validateDiagnosticReply(value,p.diagnostics)
      }catch(error) {
        clearTimeout(p.timer);this.pending.delete(requestId);this.pruneAckGroups()
        p.reject(error instanceof Error?error:new Error('Invalid bounded display reply'));return
      }
    }
    const ack = value as unknown as RenderAck
    // Kit's acks cross a carb event payload, which turns an empty Python list
    // into {} and drops None fields (measured 2026-09-30: an OV view with
    // nothing selected arrived as selectedUids {} and no timeSeconds, which
    // crashed the return to Three.js and skipped its camera handover).
    if (record(ack.view)) {
      const v = ack.view as unknown as Record<string, unknown>
      ack.view = { ...ack.view, selectedUids: Array.isArray(v.selectedUids) ? v.selectedUids as string[] : [],
        timeSeconds: typeof v.timeSeconds === 'number' ? v.timeSeconds : null }
    }
    // A group begun before this logical request cannot satisfy it, including
    // delayed pieces remaining after timeout or a reused request identifier.
    if (!p || p.sequence > requestCutoff) return
    clearTimeout(p.timer)
    this.pending.delete(ack.requestId)
    this.pruneAckGroups()
    if (ack.ok || p.settleAny) p.resolve(ack)
    else p.reject(new Error(ack.error || 'Omniverse rejected the request'))
  }

  private receiveAckPart(envelope: Record<string, unknown>) {
    const { id, part, numParts, payload } = envelope
    if (typeof id !== 'string' || !id || id.length > MAX_TRANSPORT_ID_LENGTH) return
    let group = this.ackGroups.get(id)
    if (group?.invalid) return
    if (typeof part !== 'number' || !Number.isSafeInteger(part) || typeof numParts !== 'number' ||
        !Number.isSafeInteger(numParts) || numParts < 1 || numParts > MAX_ACK_PARTS || part < 1 || part > numParts || typeof payload !== 'string') {
      if (group) this.invalidateAckGroup(group)
      return
    }
    const bytes = boundedBytes(payload, MAX_ACK_PART_BYTES)
    if (bytes === null || (group && group.count !== numParts)) {
      if (group) this.invalidateAckGroup(group)
      return
    }
    if (!group) {
      if (this.ackGroups.size >= MAX_ACK_GROUPS) return
      group = { count: numParts, parts: new Map(), bytes: 0, invalid: false,
        requestCutoff: this.requestSequence,
        timer: setTimeout(() => this.dropAckGroup(id), this.timeoutMs) }
      this.ackGroups.set(id, group)
    }
    const previous = group.parts.get(part)
    if (previous !== undefined) {
      if (previous !== payload) this.invalidateAckGroup(group)
      return // Equal retransmission consumes neither memory nor another part.
    }
    if (group.bytes + bytes > MAX_ACK_BYTES || this.ackBytes + bytes > MAX_BUFFERED_ACK_BYTES) {
      this.invalidateAckGroup(group)
      return
    }
    group.parts.set(part, payload)
    group.bytes += bytes
    this.ackBytes += bytes
    if (group.parts.size !== group.count) return
    const pieces: string[] = []
    for (let number = 1; number <= group.count; number++) pieces.push(group.parts.get(number)!)
    const text = pieces.join('')
    const cutoff = group.requestCutoff
    this.dropAckGroup(id)
    let ack: unknown
    try { ack = JSON.parse(text) } catch { return }
    this.acceptAck(ack, cutoff, true)
  }

  private invalidateAckGroup(group: AckGroup) {
    this.ackBytes -= group.bytes
    group.bytes = 0
    group.parts.clear()
    // Keep only a bounded tombstone until timeout/request cleanup: following
    // pieces must not resurrect a conflicting group and forge a fresh ACK.
    group.invalid = true
  }

  private dropAckGroup(id: string) {
    const group = this.ackGroups.get(id)
    if (!group) return
    clearTimeout(group.timer)
    this.ackBytes -= group.bytes
    this.ackGroups.delete(id)
  }

  private pruneAckGroups() {
    let earliest = Infinity
    for (const pending of this.pending.values()) earliest = Math.min(earliest, pending.sequence)
    for (const [id, group] of this.ackGroups) {
      if (group.requestCutoff < earliest) this.dropAckGroup(id)
    }
  }

  private request(send: (id: string) => Promise<void>, timeoutMs = this.timeoutMs, settleAny = false, diagnostics?:DiagnosticContext): Promise<RenderAck> {
    if (this.closed) return Promise.reject(new Error('Omniverse disconnected'))
    const id = requestUuid()
    if (this.pending.has(id)) return Promise.reject(new Error('Duplicate Omniverse request identifier'))
    const sequence = ++this.requestSequence
    return new Promise((resolve, reject) => {
      const fail = (e: Error) => {
        const pending = this.pending.get(id)
        if (!pending || pending.sequence !== sequence) return
        clearTimeout(pending.timer)
        this.pending.delete(id)
        this.pruneAckGroups()
        reject(e)
      }
      this.pending.set(id, { resolve, reject, sequence, settleAny, diagnostics,
        timer: setTimeout(() => fail(new Error('Omniverse command submission timed out')), timeoutMs) })
      void send(id).then(() => {
        const current = this.pending.get(id)
        if (!current || current.sequence !== sequence) return
        clearTimeout(current.timer)
        current.timer = setTimeout(() => fail(new Error('Omniverse acknowledgement timed out')), timeoutMs)
      }).catch(e => fail(e instanceof Error ? e : new Error(String(e))))
    })
  }
  private async command(name: string, args: object) {
    if (this.closed) throw new Error('Omniverse disconnected')
    await this.send({ command_name: name, message: JSON.stringify(args) })
  }
  loadScene(snapshot: SceneSnapshot, projectHash?: string): Promise<RenderAck> {
    validateSceneSnapshot(snapshot)
    if (projectHash !== undefined && !/^[0-9a-f]{64}$/.test(projectHash)) {
      throw new Error('Invalid project content hash for Omniverse scene')
    }
    return this.loadPacket(JSON.stringify(snapshot), 'scene', projectHash,
      sceneAckBudgetMs(this.sceneTimeoutMs, snapshot.primitives.length))
  }
  loadOverlay(packet: RenderOverlayPacket): Promise<RenderAck> {
    validateRenderOverlay(packet)
    return this.loadPacket(JSON.stringify(packet), 'overlay')
  }
  loadResultLayers(packet: ResultLayerSet, currentSource: ResultSource,
    currentTimeSeconds: number | null): Promise<RenderAck> {
    // The current identity is supplied by the verified publisher, separately
    // from the display packet. Kit checks it again before replacing USD.
    assertResultLayerCompatibility(packet, currentSource, currentTimeSeconds)
    return this.loadPacket(JSON.stringify({ packet, expectedSource: currentSource,
      expectedTimeSeconds: currentTimeSeconds }), 'result')
  }
  /** Server-owned canonical artifact; no browser numeric packet is authorized. */
  loadVerifiedResultLayers(grant:OvDisplayGrant):Promise<RenderAck> {
    const result=this.transferQueue.then(()=>this.request(id=>this.command('loadVerifiedRenderResultLayers',{requestId:id,grant}),180000))
    this.transferQueue=result.then(()=>{},()=>{});return result
  }
  clearResultLayers(displayGeneration?:number): Promise<RenderAck> {
    if(displayGeneration!==undefined&&(!Number.isSafeInteger(displayGeneration)||displayGeneration<0))throw new Error('Invalid common display generation')
    const result = this.transferQueue.then(() => this.request(id =>
      this.command('clearRenderResultLayers', { requestId: id,...(displayGeneration===undefined?{}:{displayGeneration}) })))
    this.transferQueue = result.then(() => {}, () => {})
    return result
  }
  private loadPacket(text: string, kind: 'scene' | 'overlay' | 'result', projectHash?: string,
    ackBudgetMs = this.sceneTimeoutMs): Promise<RenderAck> {
    if (text.length > 128 * 1024 * 1024) throw new Error(`Render ${kind} exceeds the 128 MiB transfer limit`)
    const chunks = Math.ceil(text.length / this.chunkSize)
    const names = kind === 'scene'
      ? ['beginRenderScene', 'renderSceneChunk', 'renderSceneProgress', 'loadRenderScene', 'sceneRequestId']
      : kind === 'overlay'
        ? ['beginRenderOverlay', 'renderOverlayChunk', 'renderOverlayProgress', 'loadRenderOverlay', 'overlayRequestId']
        : ['beginRenderResultLayers', 'renderResultLayersChunk', 'renderResultLayersProgress', 'loadRenderResultLayers', 'resultRequestId']
    const result = this.transferQueue.then(() => this.request(async requestId => {
      await this.command(names[0]!, { requestId, chunks })
      for (let start = 0, index = 0; start < text.length; start += this.chunkSize, index++) {
        await this.command(names[1]!, {
          requestId, index, data: text.slice(start, start + this.chunkSize),
        })
        // The SDK's send promise only confirms queue admission. Large scenes
        // otherwise overflow its data-channel buffer before Kit sees the tail.
        const received = index + 1
        if (chunks > this.progressBatchSize &&
            (received % this.progressBatchSize === 0 || received === chunks)) {
          const ack = await this.retryOnTimeout(() => this.request(id => this.command(names[2]!, {
            requestId: id, [names[4]!]: requestId, received,
          }), Math.min(this.timeoutMs, 12000)))
          if (ack.received !== received) throw new Error(`Omniverse ${kind} chunk count mismatch: expected ${received}, received ${ack.received}`)
          if (kind === 'scene') this.sceneProgress({ phase: 'sending', received, chunks })
        }
      }
      await this.command(names[3]!, { requestId,
        ...(kind === 'scene' && projectHash ? { projectHash } : {}) })
      if (kind === 'scene') this.sceneProgress({ phase: 'building', received: chunks, chunks })
      if (kind === 'scene') void this.recoverSceneAck(requestId)
    }, ackBudgetMs))
    this.transferQueue = result.then(() => {}, () => {})
    return result
  }
  /** Re-issue an idempotent query whose ack timed out; any other failure
   * (a rejection, a count mismatch, a closed client) is final. */
  private async retryOnTimeout(attempt: () => Promise<RenderAck>): Promise<RenderAck> {
    for (let i = 1; ; i++) {
      try { return await attempt() } catch (e) {
        if (this.closed || i >= this.progressAttempts || !(e instanceof Error) ||
            !/acknowledgement timed out|submission timed out/.test(e.message)) throw e
      }
    }
  }
  /** While a scene ack is outstanding, ask Kit which scene it last authored;
   * once that is this request, settle it from Kit's own report (same fields
   * as the scene ack). Kit answers only between frames, so before authoring
   * ends it still reports the previous scene and nothing is settled early. */
  private async recoverSceneAck(requestId: string) {
    const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
    await wait(this.sceneRecoverAfterMs)
    while (!this.closed && this.pending.has(requestId)) {
      try {
        const state = await this.request(id => this.command('getViewState', { requestId: id }),
          Math.max(1, this.sceneRecoverEveryMs - 1))
        if (state.sceneRequestId === requestId && this.pending.has(requestId)) {
          this.acceptAck({ ...state, requestId, recovered: true }, Infinity, true)
          return
        }
      } catch { /* a probe lost the same way is expected; keep asking */ }
      await wait(this.sceneRecoverEveryMs)
    }
  }
  updateView(view: RenderViewState): Promise<RenderAck> {
    return this.request(id => this.command('updateViewState', { requestId: id, view }))
  }
  /** Prove the Kit->browser custom-message path is live before real work.
   *
   * The livestream WebRTC plugin drops Kit's outbound app messages for a
   * while after a session starts although inbound commands already arrive
   * (measured 2026-09-30 on 40901: every outbound message in the first ~2 s
   * of a session through the SSH tunnel was lost, the scene ack included), so
   * the first acked request of a session could vanish although Kit sent it.
   * getViewState is idempotent: probe it on a short timeout until any ack —
   * even ok=false — comes back. The final failure keeps the "acknowledgement
   * timed out" wording so the degraded-display policy still recognises it. */
  async awaitOutboundReady(attempts = 40, perAttemptMs = 1500): Promise<RenderAck> {
    let last = ''
    for (let i = 0; i < attempts; i++) {
      try {
        return await this.request(id => this.command('getViewState', { requestId: id }), perAttemptMs, true)
      } catch (e) {
        last = e instanceof Error ? e.message : String(e)
        if (this.closed) throw e
      }
    }
    throw new Error(`Omniverse acknowledgement timed out: the Kit->browser channel never opened after ${attempts} probes (${last})`)
  }
  readView(): Promise<RenderAck> {
    return this.request(id => this.command('getViewState', { requestId: id }))
  }
  /** Only the explicit return selector opts into this bounded readonly branch. */
  readViewDiagnostics(context:DiagnosticContext):Promise<DiagnosticReply> {
    if(this.diagnosticInFlight)return Promise.reject(new Error('Bounded display readback already pending'))
    const owned=captureDiagnosticContext(context)
    this.diagnosticInFlight=true
    return this.request(id=>this.send({command_name:'getViewState',message:diagnosticRequest(id,owned)}).then(()=>{}),3000,false,owned)
      .then(ack=>ack as DiagnosticReply).finally(()=>{this.diagnosticInFlight=false})
  }
  /** Surface slice (Stage 3). Render-path command: acked, exact key set — the
   * bridge validates {requestId,visible,origin,normal,radius,field,domain}. */
  setSlice(slice: OvSliceParams): Promise<RenderAck> {
    return this.request(id => this.command('setSlice', { requestId: id, ...slice }))
  }
  /** Read back the slice state Kit actually holds (viewport drags win over
   * stale panel numbers, so the panel re-syncs from this). */
  getSliceState(): Promise<RenderAck> {
    return this.request(id => this.command('getSliceState', { requestId: id }))
  }
  /** Load a result into Kit by a server-signed grant (the browser never names
   * a path). Conversion + import of a large result can take minutes. */
  loadResultDataset(grant: OvDisplayGrant): Promise<RenderAck> {
    return this.request(id => this.command('loadResultDataset', { requestId: id, grant }), 300000)
  }
  /** Acked volume display: rebuilds the IndeX volume on the dataset's own
   * colour domain (or an explicit one) and replies with what IndeX was given. */
  setVolumeDisplay(field: 'off' | 'T' | 'U', domain: [number, number] | null): Promise<RenderAck> {
    return this.request(id => this.command('setVolumeDisplay', { requestId: id, field, domain }), 180000)
  }
  /** Liquid results: Kit reads the granted result.json itself, re-checks its
   * hash and binds it to the acknowledged scene; the browser never sends
   * colours, only frame numbers. */
  loadLiquidResult(grant: OvDisplayGrant): Promise<RenderAck> {
    return this.request(id => this.command('loadLiquidResult', { requestId: id, grant }), 60000)
  }
  setLiquidFrame(sample: number, domains: { network?: [number, number]; chips?: [number, number] } | null): Promise<RenderAck> {
    // Generous: a frame queued behind a genuine scene resync (~90 s on
    // Shinaide) must not fail; later frames supersede it anyway (latest-wins).
    return this.request(id => this.command('setLiquidFrame', { requestId: id, sample, domains }), 150000)
  }
  clearLiquidColours(): Promise<RenderAck> {
    return this.request(id => this.command('clearLiquidColours', { requestId: id }))
  }
  /** Display-only fire-and-forget commands inherited from the reference demo
   * (volume / streamlines / seed sphere / viewport sync). The OV panel treats
   * them optimistically; getSliceState/readView stay the confirmation path. */
  fireCommand(name: 'setVolume' | 'setVolumeRange' | 'setStreamlines' | 'setSeedSphere' | 'syncFromViewport',
    args: object): Promise<void> {
    return this.command(name, args)
  }
  dispose() {
    this.closed = true
    this.editEventHandler = null
    for (const p of this.pending.values()) {
      clearTimeout(p.timer)
      p.reject(new Error('Omniverse disconnected'))
    }
    this.pending.clear()
    for (const id of this.ackGroups.keys()) this.dropAckGroup(id)
  }
}
