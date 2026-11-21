import type { RenderAck } from './ovProtocol'
import type { SceneSnapshot } from './sceneSnapshot'
export type SceneTransmission = { packet: SceneSnapshot; hash: string }
/** A project loaded during an initial transfer must reach Kit before ready.
 * Each payload keeps the hash captured with it, including across awaits. */
export async function syncLatestOvScene(
  capture: () => SceneTransmission,
  load: (packet: SceneSnapshot, hash: string) => Promise<RenderAck>,
  currentHash: () => string,
  disposed: () => boolean,
): Promise<{ack: RenderAck; packet: SceneSnapshot} | null> {
  while (!disposed()) {
    const {packet, hash} = capture()
    const ack = await load(packet, hash)
    if (disposed()) return null
    if (currentHash() === hash) return {ack, packet}
  }
  return null
}
