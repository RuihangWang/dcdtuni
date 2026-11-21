import type { Construction } from '../schema/cfdproj'

export interface LiquidTransportInput {
  modelIds: string[]
  models: Construction[]
  /** Explicit wire metadata for V4 own kind properties omitted by structuredClone. */
  modelKinds?: { uid: string; kind: unknown; enumerable: boolean }[]
  state: { uid: string; constructions: unknown }
  matrixElements: number[]
}
export interface LiquidTransportProduct {
  filename: string
  carrierUid: string
  contributors: string[]
  dependencies: [string, string][]
  regions: { model: Construction; solidName: string; block: string }[]
  triangleCount: number
  volumeM3: number
  stl: string
}
export interface PreparedLiquidTransport {
  input: LiquidTransportInput
  product: LiquidTransportProduct | null
}
