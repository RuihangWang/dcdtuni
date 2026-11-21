export interface StructuralPart {
  partId: string
  faceId: string
  exportName: string
  refIds: string[]
  islandIndex?: number
  wallIndex?: number
  isBaseWall: boolean
  positions: Float32Array
  normals: Float32Array
  uvs: Float32Array
  indices: Uint16Array | Uint32Array
  materialRecipe: {
    role: 'structural-patch' | 'structural-wall'
    uvMode: 'raw'
    color: string
    opacity: number
    source: { ownerUid: string; faceId: string; patchIds: string[] }
  }
}
export interface GeometryProduct {
  schema: 'dcdtuni.geometry-product/v1'
  modelUid: string
  kind: 'box' | 'cdu'
  parts: StructuralPart[]
}
export interface BoxGeometryProductKernel {
  createProduct(source: Record<string, unknown>, kind: string): GeometryProduct
}
