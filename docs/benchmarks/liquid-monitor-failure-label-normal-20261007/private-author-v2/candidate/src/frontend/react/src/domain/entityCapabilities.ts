import type {EntityDefinition} from './BaseEntity'
export type OperationAvailability = 'implemented' | 'legacy-delegate' | 'none' | 'unsupported'
export interface EntityCapabilities {
  readonly record: 'typed' | 'opaque'
  readonly geometryProduct: OperationAvailability
  readonly materialRecipe: OperationAvailability
  readonly carrierGeometry: OperationAvailability
  readonly geometryScope: 'standalone' | 'parent-assembly' | 'none'
  readonly pose: OperationAvailability
  readonly portFrame: OperationAvailability
  readonly physicalParameters: OperationAvailability
  readonly physicalDeclarations: 'record-view'
  readonly airBoundaryFragment: OperationAvailability
  readonly airBoundaryScope: 'export-solid' | 'none'
  readonly admission: 'availability-only; caller validates input and scientific gates'
}
const legacyKinds = new Set(['custom','surface','wall','floor','partitionWall','rack','row','server','acu',
  'pipe_segment','pipe_junction','pipe','sensor','heat_gain','heat_source','crh','cold_plate','room'])
const assemblyKinds = new Set(['rackServer','rackPartition','chip','coldPlate','rdhx','heatExchanger','pump','fan'])
/** Implementation availability is separate from schema/physical admission and source ownership. */
export function entityCapabilities(definition: EntityDefinition | undefined, supported = true): Readonly<EntityCapabilities> {
  const typed = !!definition && supported && definition.family !== 'unknown'
  const kind = definition?.kind
  const own = typed && (kind === 'box' || kind === 'cdu' || kind === 'baffle')
  const legacy = typed && !!kind && legacyKinds.has(kind)
  const assembly = typed && !!kind && assemblyKinds.has(kind)
  const carrier = own ? 'implemented' : legacy || assembly ? 'legacy-delegate' : typed ? 'none' : 'unsupported'
  const product = own ? 'implemented' : legacy || assembly ? 'unsupported' : typed ? 'none' : 'unsupported'
  const hasPose = typed && ((definition?.geometryStrategy !== 'none' && definition?.geometryStrategy !== 'record'
    && definition?.type !== 'sensors' && definition?.type !== 'heatGains') || definition?.type === 'rackServer')
  return Object.freeze({
    record: typed ? 'typed' : 'opaque', geometryProduct: product, materialRecipe: product,
    carrierGeometry: carrier, geometryScope: assembly ? 'parent-assembly' : own || legacy ? 'standalone' : 'none',
    pose: hasPose ? 'implemented' : 'none',
    portFrame: typed && (kind === 'rack' || kind === 'cdu') ? 'implemented' : 'none',
    physicalParameters: typed && kind === 'coldPlatePair' ? 'implemented' : typed ? 'none' : 'unsupported',
    physicalDeclarations: 'record-view',
    airBoundaryFragment: typed && kind === 'baffle' ? 'legacy-delegate' : 'unsupported',
    airBoundaryScope: typed && kind === 'baffle' ? 'export-solid' : 'none',
    admission: 'availability-only; caller validates input and scientific gates'
  })
}
