import type { StructuralPart } from './box_geometry_product.js'
export interface MaterialRole { set: string; roughness: number; metalness: number; normalScale: number; tileM: number; color: number }
export interface MaterialContext {
  libraryAvailable?: boolean
  paintedSteelRole?: MaterialRole
  meanRoughness?: number
  tileSize?: number
  clampToEdge?: boolean
  patchImageSource?: string | null
}
export interface MaterialPolicy {
  model: 'library' | 'standard'
  objectKind: string
  parameters: { color?: string | number; side: 'double' | 'front'; polygonOffset: boolean; polygonOffsetFactor: number;
    transparent: boolean; opacity: number; depthWrite: boolean; roughness?: number; metalness?: number }
  pbr: { roughness: number; metalness: number; normalScale: number; role?: string; set?: string | null;
    defaultColor?: number; tileM?: number; boundRoughness?: number }
  texture: { source: string | null; repeat: number }
  uv: { normalize: { type: string; tileSize?: number; uCount?: number } | null; physicalScale: number | null }
  renderOrder: number
}
export interface MaterialRecipe {
  schema: 'dcdtuni.material-recipe/v1'; kind: 'box' | 'cdu'
  source: { ownerUid: string; partId: string; faceId: string; patchIds: string[] }
  options: Record<string, unknown>
  policy: MaterialPolicy
}
export interface BoxMaterialRecipeKernel {
  boxPreset(): { metalness: number; roughness: number; normalScale: number; tileM: number; set: string | null; baseColor: string }
  paintedSteelRole(): MaterialRole
  paintedSteelSet(): { meanRoughness: number; tintable: boolean }
  scalarRoughness(wanted: number, mean: number): number
  textureRepeat(options: Record<string, any>, context?: MaterialContext): number
  overlayDefaults(options: Record<string, any>): { polygonOffsetFactor?: number; keepUserColor?: boolean }
  imageSource(value: Record<string, any> | null | undefined): string | null
  materialPolicy(typeKey: string, options?: Record<string, any>, context?: MaterialContext): MaterialPolicy
  createRecipe(source: Record<string, unknown>, resolvedKind: string, part: StructuralPart, context?: MaterialContext): MaterialRecipe
}
