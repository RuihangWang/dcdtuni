import Ajv from 'ajv'
import schema from '../../../shared/schema/render-scene.schema.json'
import type { SceneSnapshot } from './sceneSnapshot'

const validate = new Ajv({ strict: true }).compile(schema)

export function validateSceneSnapshot(value: unknown): asserts value is SceneSnapshot {
  if (!validate(value)) throw new Error(`Render scene schema: ${new Ajv().errorsText(validate.errors)}`)
  const packet = value as unknown as SceneSnapshot
  const seen = new Set<string>()
  for (const p of packet.primitives) {
    if (seen.has(p.id)) throw new Error(`Duplicate render primitive ${p.id}`)
    seen.add(p.id)
    const count = p.positions.length / 3
    if (!Number.isInteger(count) || p.indices.some(i => i >= count)) throw new Error(`Invalid vertex indices: ${p.id}`)
    if (p.normals && p.normals.length !== p.positions.length) throw new Error(`Invalid normals: ${p.id}`)
    if (p.colors && p.colors.length !== p.positions.length) throw new Error(`Invalid colours: ${p.id}`)
    if (p.uvs && p.uvs.length !== count * 2) throw new Error(`Invalid UVs: ${p.id}`)
    if (p.indices.length % (p.kind === 'triangles' ? 3 : p.kind === 'lines' ? 2 : 1)) throw new Error(`Incomplete primitive: ${p.id}`)
    for (const key of ['map', 'normalMap', 'roughnessMap', 'metalnessMap'] as const) {
      const id = p.material[key]
      if (id && (!packet.textures[id] || !p.uvs)) throw new Error(`Unresolved texture ${id}`)
    }
  }
}
