import Ajv from 'ajv'
import schema from '../../../shared/schema/render-overlay.schema.json'
import type { RenderOverlayPacket } from './meshPreviewOverlay'

const validator = new Ajv({ strict: true }).compile(schema)

export function validateRenderOverlay(value: unknown): asserts value is RenderOverlayPacket {
  if (!validator(value)) throw new Error(`Render overlay schema: ${new Ajv().errorsText(validator.errors)}`)
  const packet = value as unknown as RenderOverlayPacket
  const ids = new Set<string>()
  for (const primitive of packet.primitives) {
    if (ids.has(primitive.id)) throw new Error(`Duplicate overlay primitive ${primitive.id}`)
    ids.add(primitive.id)
    const count = primitive.positions.length / 3
    if (!Number.isInteger(count) || primitive.indices.some(index => index >= count)) {
      throw new Error(`Invalid overlay vertex index: ${primitive.id}`)
    }
    if (primitive.indices.length % (primitive.kind === 'triangles' ? 3 : 2)) {
      throw new Error(`Incomplete overlay primitive: ${primitive.id}`)
    }
  }
}
