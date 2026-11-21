import type { CfdProject } from '../schema/cfdproj'
import { objectRecord, type BaseEntity, type EntityRecord } from './BaseEntity'
import { CONSTRUCTION_BUCKET_TYPES, LEGACY_EXPORT_BUCKETS, SINGLETON_TYPES, entityRegistry } from './entityRegistry'

const escapePath = (key: string) => key.replace(/~/g, '~0').replace(/\//g, '~1')
function pathOf(parts: readonly string[]): string { return '/' + parts.map(escapePath).join('/') }

/** A cheap derived view: no methods or descriptors are attached to project JSON. */
export class DomainProject {
  constructor(readonly document: CfdProject) {}
  private entity(type: string, value: unknown, parts: string[], key: string, parent?: BaseEntity): BaseEntity | null {
    const record = objectRecord(value)
    if (!record) return null
    const ownUid = typeof record.uid === 'string' && record.uid ? record.uid
      : typeof record.id === 'string' && record.id ? record.id : key
    return entityRegistry.create(type, {
      uid: ownUid, path: pathOf(parts), source: typeof record.uid === 'string' && record.uid ? 'record'
        : typeof record.id === 'string' && record.id ? 'id' : 'key',
      ...(parent ? { parentPath: parent.identity.path, parentUid: parent.uid } : {}),
    }, record)
  }
  /** Caller-selected order is retained, including insertion order inside each bucket. */
  topLevel(bucketOrder: readonly string[] = Object.keys(CONSTRUCTION_BUCKET_TYPES)): BaseEntity[] {
    const result: BaseEntity[] = []
    const constructions = objectRecord(this.document.constructions)
    for (const bucket of bucketOrder) {
      const members = objectRecord(constructions?.[bucket])
      for (const [key, value] of Object.entries(members ?? {})) {
        const entity = this.entity(bucket, value, ['constructions', bucket, key], key)
        if (entity) result.push(entity)
      }
    }
    return result
  }
  structuralForAirExport(): BaseEntity[] {
    const entities = this.topLevel(LEGACY_EXPORT_BUCKETS)
    // Unknown records remain loadable/saveable, but a declared geometric body
    // requires a registry entry before a physical snapshot can be exported.
    for (const entity of entities) {
      entity.requireSupported('air export')
      if (entity.identity.source !== 'record') throw new Error('Missing stable uid for air export: ' + entity.identity.path)
    }
    for (const entity of this.allEntities()) {
      if (!entity.supported && (entity.record.geometry !== undefined || entity.record.polygon !== undefined))
        throw new Error('Unsupported geometric bucket ' + entity.identity.path)
    }
    return entities
  }
  /** Full identity inventory is opt-in; rendering enumeration does not walk every face. */
  allEntities(): BaseEntity[] {
    const top = this.topLevel()
    const result = [...top]
    const room = objectRecord(this.document.geometry)
    if (room) result.push(entityRegistry.create('room', {
      uid: String(this.document.uid ?? '') + '/room', path: '/geometry', source: 'derived',
    }, room))
    const constructions = objectRecord(this.document.constructions)
    for (const slot of SINGLETON_TYPES) {
      const entity = this.entity(slot, constructions?.[slot], ['constructions', slot], String(this.document.uid ?? '') + '/' + slot)
      if (entity) result.push(entity)
    }
    const append = (type: string, value: unknown, parts: string[], parent?: BaseEntity) => {
      if (Array.isArray(value)) value.forEach((item, index) => {
        const entity = this.entity(type, item, [...parts, String(index)], String(index), parent)
        if (entity) { result.push(entity); walk(entity, [...parts, String(index)]) }
      })
      else for (const [key, item] of Object.entries(objectRecord(value) ?? {})) {
        const entity = this.entity(type, item, [...parts, key], key, parent)
        if (entity) { result.push(entity); walk(entity, [...parts, key]) }
      }
    }
    const appendOne = (type: string, value: unknown, parts: string[], parent?: BaseEntity) => {
      const entity = this.entity(type, value, parts, parts[parts.length - 1]!, parent)
      if (entity) { result.push(entity); walk(entity, parts) }
    }
    const appendUnknown = (type: string, value: unknown, parts: string[], parent?: BaseEntity) => {
      const record = objectRecord(value)
      if (record && (record.uid !== undefined || record.geometry !== undefined || record.polygon !== undefined))
        appendOne(type, value, parts, parent)
      else append(type, value, parts, parent)
    }
    const walk = (entity: BaseEntity, parts: string[]) => {
      const record = entity.record
      const geometry = objectRecord(record.geometry)
      const cooling = objectRecord(record.cooling)
      const liquidCooling = objectRecord(record.liquidCooling)
      const nested = objectRecord(record.constructions)
      const knownNested = new Set<string>()
      const nestedMany = (key: string, type: string) => {
        knownNested.add(key)
        append(type, nested?.[key], [...parts, 'constructions', key], entity)
      }
      const nestedOne = (key: string, type: string) => {
        knownNested.add(key)
        appendOne(type, nested?.[key], [...parts, 'constructions', key], entity)
      }
      if (entity.type === 'racks') {
        nestedMany('servers', 'rackServer')
        nestedMany('partitions', 'rackPartition')
        nestedOne('rdhx', 'rdhx')
      }
      if (entity.type === 'servers' || entity.type === 'rackServer') {
        nestedMany('chips', 'chip')
        nestedMany('coldPlates', 'coldPlate')
      }
      if (entity.type === 'acus') {
        nestedOne('heatExchanger', 'heatExchanger')
        nestedMany('fans', 'fan')
      }
      if (entity.type === 'cdus') {
        nestedOne('heatExchanger', 'heatExchanger')
        nestedOne('pump', 'pump')
        nestedOne('valve', 'valve')
      }
      for (const [key, value] of Object.entries(nested ?? {})) {
        if (!knownNested.has(key)) appendUnknown('unknownConstruction:' + key, value, [...parts, 'constructions', key], entity)
      }
      append('coldPlatePair', liquidCooling?.cpCuPairs, [...parts, 'liquidCooling', 'cpCuPairs'], entity)
      append('coolingPort', cooling?.ports, [...parts, 'cooling', 'ports'], entity)
      append('coolingPort', liquidCooling?.ports, [...parts, 'liquidCooling', 'ports'], entity)
      append('valve', cooling?.valves, [...parts, 'cooling', 'valves'], entity)
      append('pipeWaypoint', geometry?.waypoints, [...parts, 'geometry', 'waypoints'], entity)
      append('modelFace', geometry?.faces, [...parts, 'geometry', 'faces'], entity)
      append('modelFace', record.faces, [...parts, 'faces'], entity)
      append('geometryCutout', geometry?.cutouts, [...parts, 'geometry', 'cutouts'], entity)
      append('surfacePatch', record.patches, [...parts, 'patches'], entity)
      if (entity.type === 'liquidFlowNetworks') {
        append('liquidNetworkNode', record.nodes, [...parts, 'nodes'], entity)
        append('liquidNetworkEdge', record.edges, [...parts, 'edges'], entity)
      }
    }
    for (const entity of [...result]) walk(entity, entity.identity.path.slice(1).split('/').map(s => s.replace(/~1/g, '/').replace(/~0/g, '~')))
    for (const [bucket, members] of Object.entries(constructions ?? {})) {
      if (Object.prototype.hasOwnProperty.call(CONSTRUCTION_BUCKET_TYPES, bucket) || SINGLETON_TYPES.includes(bucket as typeof SINGLETON_TYPES[number])) continue
      appendUnknown(bucket, members, ['constructions', bucket])
    }
    const instrumentation = objectRecord((this.document as unknown as EntityRecord).instrumentation)
    append('instrumentProbe', instrumentation?.probes, ['instrumentation', 'probes'])
    append('instrumentMeasurement', instrumentation?.measurements, ['instrumentation', 'measurements'])
    const models = objectRecord((this.document as unknown as EntityRecord).models)
    for (const [group, definitions] of Object.entries(models ?? {})) {
      for (const [kind, records] of Object.entries(objectRecord(definitions) ?? {})) {
        const fields = objectRecord(records)
        // Model-library dictionaries vary by group: a definition is identified by
        // its actual path, never by an invented construction or solver UID.
        if (fields && ('uid' in fields || 'type' in fields || 'geometry' in fields)) append('modelDefinition', { [kind]: fields }, ['models', group])
        else append('modelDefinition', records, ['models', group, kind])
      }
    }
    return result
  }
}
