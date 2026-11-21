import { useEffect, useId, useRef, useState } from 'react'
import type { SceneSnapshot } from './sceneSnapshot'
import { OvCommandClient, ovAckTimeoutDegraded, type RenderAck, type SceneProgress } from './ovProtocol'
import { syncLatestOvScene } from './ovSceneTransfer'
import { createOvStartReceipt, ovStreamLeases, terminateOvViewer } from './ovStreamLease'
import { useTranslation } from '../useTranslation'
import type { TranslationKey } from '../i18n'

export interface OvViewportProps {
  server: string
  signalingPort?: number
  snapshot: () => SceneSnapshot
  projectHash: () => string
  /** ack is null in degraded display mode: video live, confirmation channel dead. */
  onReady?: (client: OvCommandClient, ack: RenderAck | null, snapshot: SceneSnapshot) => void
  onError?: (error: Error) => void
}

/** Mount over the retained Three viewport. Failure never mutates the project or local scene. */
export function OvViewport(props: OvViewportProps) {
  const {t}=useTranslation()
  const id = useId().replace(/:/g, '')
  const latest = useRef(props)
  latest.current = props
  const [status, setStatus] = useState<TranslationKey|null>('renderer.connecting')
  const [error, setError] = useState('')
  const [progress, setProgress] = useState<SceneProgress | null>(null)
  useEffect(() => {
    let disposed = false
    let client: OvCommandClient | null = null
    let lease: ReturnType<typeof ovStreamLeases.open> | null = null
    let started = false
    const startReceipt = createOvStartReceipt()
    let lastStats = -Infinity
    let customEvents = 0
    // Keep a timestamped, small diagnostic trail without changing connection
    // policy, UI state or logging scene/texture payloads and session tokens.
    function diagnostic(phase: string, message: unknown) {
      const event = message && typeof message === 'object'
        ? message as { action?: unknown; status?: unknown; info?: unknown; stats?: unknown }
        : {}
      const metrics: Record<string, string | number> = {}
      if (phase === 'stats' && event.stats && typeof event.stats === 'object') {
        const values = event.stats as Record<string, unknown>
        for (const name of ['codec', 'fps', 'rtd', 'avgDecodeTime', 'frameLoss', 'packetLoss',
          'totalBandwidth', 'currentBitrate', 'utilizedBandwidth',
          'streamingResolutionWidth', 'streamingResolutionHeight']) {
          const value = values[name]
          if (typeof value === 'number' && Number.isFinite(value)) metrics[name] = value
          else if (name === 'codec' && typeof value === 'string') metrics[name] = value.slice(0, 80)
        }
      }
      console.info('[dcdtuni:ov]', JSON.stringify({ phase, at: new Date().toISOString(),
        action: typeof event.action === 'string' || typeof event.action === 'number' ? event.action : undefined,
        status: typeof event.status === 'string' || typeof event.status === 'number' ? event.status : undefined,
        info: String(event.info ?? '').slice(0, 500),
        ...(phase === 'stats' ? { stats: metrics } : {}) }))
    }
    const timeout = setTimeout(() => fail(new Error('Omniverse connection timed out')), 15000)
    function fail(error: Error) {
      if (disposed) return
      clearTimeout(timeout)
      diagnostic('failure', { info: error.message })
      setStatus(null)
      setError(error.message)
      client?.dispose()
      latest.current.onError?.(error)
    }
    void import('@nvidia/omniverse-webrtc-streaming-library').then(async ({ AppStreamer, StreamStatus }) => {
      if (disposed) return
      client = new OvCommandClient(payload => AppStreamer.sendMessage({ event_type: 'send_message_from_event', payload }))
      lease = ovStreamLeases.open(() => AppStreamer.connect({
        streamSource: 'direct',
        streamConfig: {
          videoElementId: `${id}-video`, audioElementId: `${id}-audio`,
          signalingServer: props.server, signalingPort: props.signalingPort ?? 8301,
          width: 1600, height: 900, fps: 30, authenticate: false,
          nativeTouchEvents: true, maxReconnects: 0,
          onUpdate: (message: unknown) => diagnostic('update', message),
          onStreamStats: (message: unknown) => {
            const now = performance.now()
            if (now - lastStats >= 1000) {
              lastStats = now
              diagnostic('stats', message)
            }
          },
          onCustomEvent: (message: unknown) => {
            // Keep transport diagnostics bounded: only ACK metadata, never
            // scene chunks, textures or complete SDK envelopes.
            const event = message && typeof message === 'object'
              ? message as { event_type?: unknown; payload?: unknown; part?: unknown; numParts?: unknown }
              : {}
            if (customEvents++ < 3) {
              console.info('[dcdtuni:ov]', JSON.stringify({ phase: 'custom-shape',
                keys: Object.keys(event).slice(0, 10), eventType: event.event_type,
                payloadType: typeof event.payload }))
            }
            if (event.event_type === 'dcdtuni:renderAck') {
              const payload = event.payload && typeof event.payload === 'object'
                ? event.payload as { requestId?: unknown; ok?: unknown; received?: unknown } : {}
              const received = typeof payload.received === 'number' ? payload.received : undefined
              if (received === undefined || received === 64 || received % 512 === 0) {
                diagnostic('ack', { info: JSON.stringify({
                  requestId: typeof payload.requestId === 'string' ? payload.requestId : undefined,
                  ok: typeof payload.ok === 'boolean' ? payload.ok : undefined,
                  received, part: event.part, numParts: event.numParts,
                }) })
              }
            }
            client?.receive(message)
          },
          onStart: (message: unknown) => {
            // This lifecycle receipt must survive disposal: the SDK invokes
            // its own cancellation wrapper before forwarding this callback.
            const terminal = startReceipt.record(message)
            if (!terminal || started || disposed) return
            diagnostic('start', message)
            started = true
            const result = message as {status?: unknown; info?: unknown}
            if (result.status !== 'success') {
              fail(new Error('Omniverse stream could not start: ' + String(result.info ?? result.status)))
              return
            }
            clearTimeout(timeout)
            setStatus('renderer.synchronizing')
            client!.onSceneProgress(p => { if (!disposed) setProgress(p) })
            // loadScene resolves only after validated USD authoring + camera/selection application.
            let packet: SceneSnapshot | null = null
            let packetHash = ''
            void Promise.resolve().then(async () => {
              // Do not spend the scene transfer on a path that cannot ack yet.
              const probe = await client!.awaitOutboundReady()
              diagnostic('outbound-ready', { info: JSON.stringify({ ok: probe.ok }) })
              return syncLatestOvScene(() => {
                packet = latest.current.snapshot()
                packetHash = latest.current.projectHash()
                return {packet, hash: packetHash}
              }, (next, hash) => client!.loadScene(next, hash),
              () => latest.current.projectHash(), () => disposed)
            }).then(result => {
              if (disposed || !result) return
              const {ack, packet: acknowledgedPacket} = result
              client!.onSceneProgress(null)
              setProgress(null)
              setStatus(null)
              diagnostic('scene-ack', { info: 'Validated engineering scene and view acknowledged' })
              latest.current.onReady?.(client!, ack, acknowledgedPacket)
            }).catch(e => {
              const error = e instanceof Error ? e : new Error(String(e))
              const video = document.getElementById(`${id}-video`) as HTMLVideoElement | null
              const frames = video ? video.getVideoPlaybackQuality().totalVideoFrames : 0
              if (!disposed && packet && ovAckTimeoutDegraded(error.message, frames)) {
                // Display-only fallback (product ruling: OV never edits). The
                // re-render is on screen but UNCONFIRMED: no ack means no
                // verified authoring receipt, so the caller must banner it and
                // suspend ack-dependent features (edit bridge, live re-sync,
                // camera read-back, result layers).
                // Chunk pacing waits on progress acks, which this dead channel
                // will never deliver — a paced resend would leave Kit with a
                // partial transfer and an empty viewport. Resend as one burst
                // (progressBatchSize = MAX) so authoring completes even though
                // nothing will ever confirm it; the banner says exactly that.
                const burstPacket = packet
                client?.dispose()
                const burst = new OvCommandClient(payload => AppStreamer.sendMessage({ event_type: 'send_message_from_event', payload }),
                  45000, 12000, 120000, Number.MAX_SAFE_INTEGER)
                client = burst
                void burst.loadScene(burstPacket, packetHash).catch(() => {})
                diagnostic('scene-degraded', { info: `${error.message}; decoded video frames ${frames}; burst resend issued` })
                setStatus(null)
                latest.current.onReady?.(burst, null, burstPacket)
                return
              }
              fail(error)
            })
          },
          onStop: (message: unknown) => {
            diagnostic('stop', message)
            fail(new Error('Omniverse stream stopped'))
          },
          onTerminate: (message: unknown) => {
            diagnostic('terminate', message)
            fail(new Error('Omniverse stream disconnected'))
          },
        },
      }), initialized => terminateOvViewer(() => AppStreamer.terminate(false),
        () => AppStreamer.streamStatus === StreamStatus.none, initialized, 15000, 50, startReceipt))
      await lease.ready
    }).catch(e => fail(e instanceof Error ? e : new Error(String(e))))
    return () => {
      disposed = true
      clearTimeout(timeout)
      client?.dispose()
      // Disconnect this viewer only; never terminate the shared Kit process.
      void lease?.close().catch(() => {})
    }
  }, [props.server, props.signalingPort, id])
  function statusText() {
    if (status !== 'renderer.synchronizing' || !progress) return status ? t(status) : ''
    return progress.phase === 'building' ? t('renderer.kit_building')
      : t('renderer.synchronizing_pct', { pct: Math.floor(100 * progress.received / Math.max(1, progress.chunks)) })
  }
  return <div className="ov-render-viewport" style={{ position: 'absolute', inset: 0, background: '#172027', zIndex: 5 }}>
    {/* tabIndex=-1: the streaming library turns input forwarding on from a
        focusin on this element's parent; an unfocusable video never raises
        one, so viewport clicks/drags (seed sphere, slice disc) never reach Kit. */}
    <video id={`${id}-video`} autoPlay playsInline muted tabIndex={-1} style={{ width: '100%', height: '100%', objectFit: 'contain', outline: 'none' }} />
    <audio id={`${id}-audio`} autoPlay />
    {(status||error) && <div role="status" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#fff' }}>{status?statusText():error}</div>}
  </div>
}
