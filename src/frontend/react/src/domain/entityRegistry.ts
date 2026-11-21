import * as entities from './BaseEntity'
import {entityCapabilities} from './entityCapabilities'
import type { BaseEntity, EntityDefinition, EntityIdentity, EntityRecord } from './BaseEntity'

/** Registry version is independent of the persisted cfdproj schema version. */
export const ENTITY_REGISTRY_VERSION = 1 as const
export const CONSTRUCTION_BUCKET_TYPES = {
  boxes: 'box', customMeshes: 'custom', surfaces: 'surface', walls: 'wall',
  partitionWalls: 'partitionWall', baffles: 'baffle', floors: 'floor',
  racks: 'rack', rows: 'row', servers: 'server', acus: 'acu', cdus: 'cdu',
  pipeSegments: 'pipe_segment', pipeJunctions: 'pipe_junction', pipes: 'pipe',
  sensors: 'sensor', liquidFlowNetworks: 'liquidFlowNetwork', heatGains: 'heat_gain',
} as const
/** Existing shared exporter order: networks are topology, not STL models. */
export const LEGACY_EXPORT_BUCKETS = Object.keys(CONSTRUCTION_BUCKET_TYPES).filter(b => b !== 'liquidFlowNetworks')
export const SINGLETON_TYPES = ['raisedFloor', 'falseCeiling'] as const
export const CHILD_TYPES = [
  'rackServer', 'coldPlatePair', 'coolingPort', 'valve', 'pipeWaypoint',
  'modelFace', 'surfacePatch', 'liquidNetworkNode', 'liquidNetworkEdge',
  'instrumentProbe', 'instrumentMeasurement', 'modelDefinition',
  'chip', 'coldPlate', 'rackPartition', 'rdhx', 'heatExchanger', 'pump', 'fan', 'geometryCutout',
] as const

const ENTITY_CLASSES: Record<string, typeof entities.BaseEntity> = {
  boxes: entities.BoxEntity,
  customMeshes: entities.CustomMeshEntity,
  surfaces: entities.SurfaceEntity,
  walls: entities.WallEntity,
  partitionWalls: entities.PartitionWallEntity,
  baffles: entities.BaffleEntity,
  floors: entities.FloorEntity,
  racks: entities.RackEntity,
  rows: entities.RowEntity,
  servers: entities.ServerEntity,
  acus: entities.AcuEntity,
  cdus: entities.CduEntity,
  pipeSegments: entities.PipeSegmentEntity,
  pipeJunctions: entities.PipeJunctionEntity,
  pipes: entities.PipeEntity,
  sensors: entities.SensorEntity,
  liquidFlowNetworks: entities.LiquidFlowNetworkEntity,
  heatGains: entities.HeatGainEntity,
  room: entities.RoomEntity,
  raisedFloor: entities.RaisedFloorEntity,
  falseCeiling: entities.FalseCeilingEntity,
  rackServer: entities.RackServerEntity,
  coldPlatePair: entities.ColdPlatePairEntity,
  coolingPort: entities.CoolingPortEntity,
  valve: entities.ValveEntity,
  pipeWaypoint: entities.PipeWaypointEntity,
  modelFace: entities.ModelFaceEntity,
  surfacePatch: entities.SurfacePatchEntity,
  liquidNetworkNode: entities.LiquidNetworkNodeEntity,
  liquidNetworkEdge: entities.LiquidNetworkEdgeEntity,
  instrumentProbe: entities.InstrumentProbeEntity,
  instrumentMeasurement: entities.InstrumentMeasurementEntity,
  modelDefinition: entities.ModelDefinitionEntity,
  pdu: entities.PduEntity,
  chip: entities.ChipEntity,
  coldPlate: entities.ColdPlateEntity,
  rackPartition: entities.RackPartitionEntity,
  rdhx: entities.RdhxEntity,
  heatExchanger: entities.HeatExchangerEntity,
  pump: entities.PumpEntity,
  fan: entities.FanEntity,
  geometryCutout: entities.GeometryCutoutEntity,
}

function definition(type: string, kind: string, family: EntityDefinition['family'],
  geometryStrategy: EntityDefinition['geometryStrategy'], aliases: readonly string[] = [kind]): EntityDefinition {
  return Object.freeze({ type, version: 1, kind, family, geometryStrategy, aliases: Object.freeze([...aliases]) })
}
export class EntityRegistry {
  readonly version = ENTITY_REGISTRY_VERSION
  private readonly definitions = new Map<string, EntityDefinition>()
  constructor() {
    for (const [bucket, kind] of Object.entries(CONSTRUCTION_BUCKET_TYPES)) {
      const equipment = ['racks', 'servers', 'acus', 'cdus'].includes(bucket)
      const surface = ['surfaces', 'walls', 'partitionWalls', 'baffles', 'floors'].includes(bucket)
      const pipe = ['pipeSegments', 'pipes'].includes(bucket)
      const network = bucket === 'liquidFlowNetworks'
      this.definitions.set(bucket, definition(bucket, kind,
        network ? 'network' : equipment ? 'equipment' : 'geometry',
        network ? 'none' : surface ? 'surface' : pipe ? 'polyline' : bucket === 'customMeshes' ? 'imported' : 'body',
        bucket === 'boxes' ? ['box', 'heat_source', 'crh', 'cold_plate', bucket] : [kind, bucket]))
    }
    this.definitions.set('room', definition('room', 'room', 'geometry', 'room'))
    this.definitions.set('pdu', definition('pdu', 'box', 'equipment', 'body', ['box']))
    for (const type of SINGLETON_TYPES) this.definitions.set(type, definition(type, type, 'geometry', 'surface'))
    for (const type of CHILD_TYPES) this.definitions.set(type, definition(type, type,
      type === 'modelDefinition' ? 'definition' : 'child', 'record'))
  }
  types(): readonly string[] { return [...this.definitions.keys()] }
  capabilities(type: string, version: number = 1) { return entityCapabilities(this.resolve(type,version)) }
  resolve(type: string, version: number = 1): EntityDefinition | undefined {
    return version === this.version ? this.definitions.get(type) : undefined
  }
  create(type: string, identity: EntityIdentity, record: EntityRecord): BaseEntity {
    const entry = this.resolve(type)
    const meta = record.meta !== null && typeof record.meta === 'object' ? record.meta as EntityRecord : null
    const declaredKind = typeof meta?.legacyKind === 'string' ? meta.legacyKind
      : typeof record.kind === 'string' ? record.kind : undefined
    const supported = !!entry && (!declaredKind || entry.family === 'child' || entry.family === 'definition'
      || type === 'room' || SINGLETON_TYPES.includes(type as typeof SINGLETON_TYPES[number])
      || entry.aliases.includes(declaredKind))
    const def = entry ?? definition(type, declaredKind ?? type, 'unknown', 'none')
    // Preserve legacy BOX subtypes without rewriting their source records.
    const effective = declaredKind && supported && def.aliases.includes(declaredKind)
      ? Object.freeze({ ...def, kind: declaredKind }) : def
    if (!supported) return new entities.OpaqueEntity(
      declaredKind ? Object.freeze({ ...def, kind: declaredKind }) : def, identity, record, false)
    const asset = entities.objectRecord(record.assetDefinition)
    const Type = type === 'boxes' && asset?.type === 'pdu' ? entities.PduEntity : ENTITY_CLASSES[type]
    if (!Type) return new entities.OpaqueEntity(effective, identity, record, false)
    return new Type(effective, identity, record)
  }
}
export const entityRegistry = new EntityRegistry()
