import * as moduleNamespace from '../../../shared/js/model_pose_ports.js'
import type { ModelPosePorts } from '../../../shared/js/model_pose_ports.js'

type KernelGlobal = typeof globalThis & {
  modelPosePorts?: Readonly<ModelPosePorts>
  window?: Window & { modelPosePorts?: Readonly<ModelPosePorts> }
}
// The shared module has one numeric implementation: CJS exposes its default;
// classic scripts/Vite install the same API on the compatibility global.
const imported = (moduleNamespace as { default?: Readonly<ModelPosePorts> }).default
const root = globalThis as KernelGlobal
const kernel = imported ?? root.modelPosePorts ?? root.window?.modelPosePorts
if (!kernel) throw new Error('model_pose_ports.js is not loaded')
export const modelPoseKernel: Readonly<ModelPosePorts> = kernel
export type { NumericPoint, ModelPose, EquipmentPortFrame } from '../../../shared/js/model_pose_ports.js'
