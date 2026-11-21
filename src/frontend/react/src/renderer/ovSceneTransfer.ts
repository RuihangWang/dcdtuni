import type { RenderAck } from './ovProtocol'
import type { SceneSnapshot } from './sceneSnapshot'
/** `key`: the packet's sceneKeyOf when the caller already has it. */
export type SceneTransmission = { packet: SceneSnapshot; hash: string; key?: string }
/** A project loaded during an initial transfer must reach Kit before ready.
 * Each payload keeps the hash captured with it, including across awaits. */
export async function syncLatestOvScene(
  capture: () => SceneTransmission,
  load: (packet: SceneSnapshot, hash: string, key?: string) => Promise<RenderAck>,
  currentHash: () => string,
  disposed: () => boolean,
): Promise<{ack: RenderAck; packet: SceneSnapshot} | null> {
  while (!disposed()) {
    const {packet, hash, key} = capture()
    const ack = await load(packet, hash, key)
    if (disposed()) return null
    if (currentHash() === hash) return {ack, packet}
  }
  return null
}

/** Whether Kit can still hold this project's scene: its camera-handoff probe
 * names the project it holds, and refuses the read when it holds none. */
export function kitMayHoldScene(probe: RenderAck, projectHash: string): boolean {
  return probe.ok === true && probe.projectHash === projectHash
}

/** Post the scene to the server, which hands it to Kit (Kit's bridge,
 * scene_handoff.py) to author while this page's stream is still connecting.
 * True when the server took it; then a reuse request for its key waits for
 * that authoring in Kit instead of being refused. */
export async function handOffScene(text: string, projectHash: string, sceneKey: string, signal?: AbortSignal): Promise<boolean> {
  if (typeof CompressionStream === 'undefined') return false
  const body = await new Response(new Response(text).body!.pipeThrough(new CompressionStream('deflate'))).arrayBuffer()
  const response = await fetch('/api/ov/scene-handoff', {
    method: 'POST', body, signal,
    headers: { 'Content-Type': 'application/octet-stream', 'X-Project-Hash': projectHash, 'X-Scene-Key': sceneKey },
  })
  return response.ok
}
