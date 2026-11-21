/** Types for js/material_library.js — the reader of textures/materials.json. */
export interface PoolFile { file: string; format: 'jpeg' | 'png'; size: [number, number]; bytes: number; sha256: string }
export interface TextureSet {
  label: string
  source: { kind: 'generated' | 'photo' | 'project'; recipe?: string; seed?: number; origin?: string; url?: string; license: string }
  tintable: boolean
  alphaInColor?: boolean
  maps: { color: PoolFile; normal: PoolFile; roughness: PoolFile }
  stats: { meanLuminance: number; medianLuminance: number; minLuminance: number; meanRoughness: number; meanAlpha?: number }
}
export type UvMode = 'physical' | 'stretch' | 'tile-per-u'
export interface LibraryMaterial {
  kind: string
  label: string
  role: string | null
  hueInMap: boolean
  library?: boolean
  set: string | null
  baseColor: string
  unboundColor?: string
  colorSource: 'preset' | 'model'
  factoryColors?: string[]
  tintable: boolean
  roughness: number
  metallic: number
  normalScale: number
  tileM: number | null
  uv: UvMode
  wrap: 'repeat' | 'clamp'
  doubleSide: boolean
  opacity: number
}
export interface CameraLight { role: string; color: string; intensity: number; direction: [number, number, number] }
export interface Lighting {
  toneMapping: 'aces-filmic' | 'agx' | 'neutral' | 'none'
  outputColorSpace: 'srgb'
  exposure: number
  intensityUnit: string
  /** cabinetShare: Three.js only, the environment's share inside rack cabinets (RTX occludes its dome there). */
  environment: { type: 'room'; intensity: number; blurSigma: number; cabinetShare: { lining: number; opening: number } }
  cameraLights: CameraLight[]
  ambient: { color: string; intensity: number }
  /** The Kit rig (dome, key and fill lights; sRGB hex colours; rotateXYZ degrees) and RTX exposure. */
  kit: {
    domeIntensity: number; domeColor: string; domeSpecular: number
    keyIntensity: number; keyColor: string; keyRotateXYZ: [number, number, number]; keyAngle: number
    fillIntensity: number; fillColor: string; fillRotateXYZ: [number, number, number]; fillAngle: number
    shadows: boolean; autoExposure: boolean; exposureCompensation: number
    /** The fixed RTX camera used when autoExposure is false. */
    fixedExposure: { filmIso: number; exposureTime: number; fNumber: number }
    /** The constant RTX backdrop (sRGB hex), the editors' viewport colour. */
    background: string
    /** Further RTX render settings (carb path -> value) pinned for an editor scene. */
    rtxSettings?: Record<string, number | boolean>
  }
  /** Named looks: scale factors on the rig (Three by camera-light role, Kit by dome/key/fill). */
  presets: Record<string, LightingPreset>
  /** The one world-fixed oblique light that casts shadows, in both renderers, when shadows are on. */
  shadow: {
    type: 'basic' | 'pcf' | 'pcf-soft'; rotateXYZ: [number, number, number]; kitAngle: number
    share: number; gain: number; environment: number; mapSize: number; bias: number; normalBias: number
  }
}
export interface LightingPreset {
  exposure: number; environment: number; key: number; fill: number; rim: number; ambient: number
  kitDome: number; kitKey: number; kitFill: number; kitExposureStops: number
}
export interface PatchPreset { id: string; roles: Array<'return' | 'supply'>; labelKey: string; image: PoolFile }
export interface MaterialManifest {
  schema: 'dcdtuni.material-library/v1'
  sets: Record<string, TextureSet>
  photos: Record<string, { label: string; image: PoolFile }>
  patchPresets: PatchPreset[]
  materials: Record<string, Record<string, unknown>>
  lighting: Lighting
}
export interface MaterialLibrary {
  readonly SCHEMA: 'dcdtuni.material-library/v1'
  install(manifest: unknown): MaterialLibrary
  isReady(): boolean
  whenReady(fn: (lib: MaterialLibrary) => void): void
  load(url: string): Promise<MaterialLibrary>
  manifest(): MaterialManifest
  material(kind: string): LibraryMaterial | null
  set(name: string): TextureSet | null
  setNames(): string[]
  setsInUse(): string[]
  libraryRoles(): string[]
  meanRoughness(set: string): number
  mapFile(set: string, map: 'color' | 'normal' | 'roughness'): string
  textureUrl(base: string, file: string): string
  parseColor(value: unknown): number | null
  colorFor<T>(kind: string, incoming: T, keepUserColor?: boolean): T | string
  roughnessScalar(kind: string, mapBound: boolean): number
  lighting(): Lighting
  patchPresets(): PatchPreset[]
  photos(): MaterialManifest['photos']
  poolFile(url: string): PoolFile | null
  poolRef(url: string): string | null
}
declare const materialLibrary: MaterialLibrary
export = materialLibrary
