import * as THREE from 'three'
import { modelPoseKernel } from '../domain/modelPoseKernel'
import '../../../shared/js/geometry_scene.js'
import type { LiquidTransportInput, LiquidTransportProduct } from './liquidTransportProtocol'

type Model = LiquidTransportInput['models'][number]
const scope = globalThis as unknown as {
  geometryScene: { buildLiquidTransportDetached(input: LiquidTransportInput, three: typeof THREE,
    csg: unknown, portFrame: (model: Model, port: string, end: string) => unknown): LiquidTransportProduct | null }
  require?: (name: string) => typeof THREE
  ThreeCSG: { CSG: unknown }
  onmessage: ((event: MessageEvent<LiquidTransportInput>) => void) | null
  postMessage(message: unknown): void
}
// The canonical UMD has two trusted require('three') references. Its module
// is loaded after that bridge is installed, with no copied BSP body or eval.
const previousRequire = scope.require
scope.require = name => {
  if (name === 'three') return THREE
  throw new Error(`three-csg-ts: unknown module ${name}`)
}
const engineReady = import('../../../shared/vendor/three-csg-ts.umd.js').then(() => scope.ThreeCSG.CSG)
  .finally(() => {
    if (previousRequire) scope.require = previousRequire
    else delete scope.require
  })
// No scene, DOM, window shim, eval, or WebGL context is created here.
scope.onmessage = async event => {
  try {
    const CSG = await engineReady
    const product = scope.geometryScene.buildLiquidTransportDetached(event.data, THREE, CSG,
      (model, port, end) => modelPoseKernel.equipmentPortFrame(
        model as unknown as Record<string, unknown>, model.kind as string, port, end))
    scope.postMessage({ ok: true, product })
  } catch (error) {
    scope.postMessage({ ok: false, error: error instanceof Error ? error.message : String(error) })
  }
}
