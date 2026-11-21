import { modelPoseKernel } from './modelPoseKernel'
import { geometryProductKernel } from './geometryProductKernel'
import { coldPlatePairKernel, type ColdPlateFluidContext } from './coldPlatePairKernel'

/** Renderer-independent, derived views. The source record remains plain project JSON. */
export type EntityRecord = Record<string, unknown>
export type EntityFamily = 'geometry' | 'equipment' | 'network' | 'child' | 'definition' | 'unknown'
export interface EntityDefinition {
  readonly type: string
  readonly version: 1
  readonly family: EntityFamily
  readonly kind: string
  readonly geometryStrategy: 'body' | 'surface' | 'polyline' | 'imported' | 'room' | 'record' | 'none'
  readonly aliases: readonly string[]
}
export interface EntityIdentity {
  readonly uid: string
  readonly path: string
  readonly parentPath?: string
  readonly parentUid?: string
  readonly source: 'record' | 'id' | 'key' | 'derived'
}
export function objectRecord(value: unknown): EntityRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as EntityRecord : null
}
export class BaseEntity {
  constructor(
    readonly definition: EntityDefinition,
    readonly identity: EntityIdentity,
    readonly record: EntityRecord,
    readonly supported = true,
  ) {}
  get uid(): string { return this.identity.uid }
  get type(): string { return this.definition.type }
  get kind(): string { return this.definition.kind }
  /** Snapshot only on explicit request; enumeration never clones geometry/texture payloads. */
  toRecord(): EntityRecord { return structuredClone(this.record) }
  geometry() {
    return {
      type: this.type, strategy: this.definition.geometryStrategy,
      geometry: objectRecord(this.record.geometry), polygon: this.record.polygon,
      sourcePath: this.identity.path,
    }
  }
  /** Owned structural buffers; unsupported kinds are never lowered as boxes. */
  geometryProduct() {
    this.requireSupported('geometry product')
    return geometryProductKernel.createProduct({ ...this.record, uid: this.uid }, this.definition.kind)
  }
  render() {
    const meta = objectRecord(this.record.meta)
    const display = objectRecord(meta?.display)
    return {
      type: this.type, sourcePath: this.identity.path, modelUid: this.uid,
      label: typeof meta?.name === 'string' ? meta.name : this.uid,
      // The persisted activation flag is not final viewport visibility or a
      // solver inclusion decision; renderer/physics adapters own those policies.
      enabled: meta?.enabled !== false,
      display: display ?? null,
      opacity: typeof display?.opacity === 'number' ? display.opacity : 1,
      supported: this.supported,
    }
  }
  physics() {
    return {
      type: this.type, sourcePath: this.identity.path, modelUid: this.uid,
      parentUid: this.identity.parentUid,
      declarations: {
        cfd: this.record.cfd, physics: this.record.physics,
        cooling: this.record.cooling, liquidCooling: this.record.liquidCooling,
      },
    }
  }
  requireSupported(operation: string): void {
    if (!this.supported) throw new Error('Unsupported entity ' + this.identity.path + ' for ' + operation)
  }
}
export class GeometryEntity extends BaseEntity {
  /** Persisted project-coordinate pose; physical adapters own unit conversion. */
  pose() { return modelPoseKernel.modelPose(this.record) }
  localPointToWorld(point: unknown) { return modelPoseKernel.modelLocalPointToWorld(this.record, point) }
  localDirectionToWorld(direction: unknown) { return modelPoseKernel.modelLocalDirectionToWorld(this.record, direction) }
}
export class EquipmentEntity extends GeometryEntity {}
export class RackEntity extends EquipmentEntity {
  portFrame(portType?: string, end?: string) {
    return modelPoseKernel.equipmentPortFrame(this.record, this.definition.kind, portType, end)
  }
  serverUids(): string[] {
    const constructions = objectRecord(this.record.constructions)
    return Object.entries(objectRecord(constructions?.servers) ?? {}).map(([key, value]) => {
      const record = objectRecord(value)
      return typeof record?.uid === 'string' ? record.uid : key
    })
  }
}
export class NetworkEntity extends BaseEntity {
  override physics() {
    return { ...super.physics(), topology: { nodes: this.record.nodes, edges: this.record.edges } }
  }
}
export class RoomEntity extends GeometryEntity {
  override geometry() {
    return { ...super.geometry(), geometry: this.record, polygon: this.record.plane }
  }
}
export class ChildEntity extends BaseEntity {}
export class OpaqueEntity extends BaseEntity {}

/** Named domain types share the same data contract; carriers stay in adapters. */
export class BoxEntity extends GeometryEntity {}
export class CustomMeshEntity extends GeometryEntity {}
export class SurfaceEntity extends GeometryEntity {}
export class WallEntity extends SurfaceEntity {}
export class PartitionWallEntity extends WallEntity {}
export class BaffleEntity extends SurfaceEntity {}
export class FloorEntity extends SurfaceEntity {}
export class RowEntity extends GeometryEntity {}
export class ServerEntity extends EquipmentEntity {}
export class AcuEntity extends EquipmentEntity {}
export class CduEntity extends EquipmentEntity {
  portFrame(portType?: string, end?: string) {
    return modelPoseKernel.equipmentPortFrame(this.record, this.definition.kind, portType, end)
  }
}
export class PipeSegmentEntity extends GeometryEntity {}
export class PipeJunctionEntity extends GeometryEntity {}
export class PipeEntity extends GeometryEntity {}
export class SensorEntity extends BaseEntity {}
export class LiquidFlowNetworkEntity extends NetworkEntity {}
export class HeatGainEntity extends BaseEntity {}
export class RaisedFloorEntity extends FloorEntity {}
export class FalseCeilingEntity extends SurfaceEntity {}
export class RackServerEntity extends ServerEntity {}
export class ColdPlatePairEntity extends ChildEntity {
  physicalParameters(fluid: ColdPlateFluidContext = {}) {
    return coldPlatePairKernel.parameters(this.record, fluid)
  }
  override physics(fluid: ColdPlateFluidContext = {}) {
    return { ...super.physics(), edgeType: 'cold_plate' as const, params: this.physicalParameters(fluid) }
  }
}
export class CoolingPortEntity extends ChildEntity {}
export class ValveEntity extends ChildEntity {}
export class PipeWaypointEntity extends ChildEntity {}
export class ModelFaceEntity extends ChildEntity {}
export class SurfacePatchEntity extends ChildEntity {}
export class LiquidNetworkNodeEntity extends ChildEntity {}
export class LiquidNetworkEdgeEntity extends ChildEntity {}
export class InstrumentProbeEntity extends ChildEntity {}
export class InstrumentMeasurementEntity extends ChildEntity {}
export class ModelDefinitionEntity extends ChildEntity {}
/** Only an explicit asset type hydrates this class; names do not assign physics. */
export class PduEntity extends BoxEntity {}
export class ChipEntity extends ChildEntity {}
export class ColdPlateEntity extends ChildEntity {}
export class RackPartitionEntity extends ChildEntity {}
export class RdhxEntity extends ChildEntity {}
export class HeatExchangerEntity extends ChildEntity {}
export class PumpEntity extends ChildEntity {}
export class FanEntity extends ChildEntity {}
export class GeometryCutoutEntity extends ChildEntity {}
