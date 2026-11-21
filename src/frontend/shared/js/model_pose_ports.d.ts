export interface NumericPoint { x: number; y: number; z: number }
export interface NumericQuaternion extends NumericPoint { w: number }
export interface ModelTransform { quaternion: NumericQuaternion; scale: NumericPoint }
export interface ModelPose extends ModelTransform { position: NumericPoint }
export interface EquipmentPortFrame {
  position: NumericPoint; direction: NumericPoint
  localPosition: NumericPoint; localDirection: NumericPoint
}
export interface ModelPosePorts {
  getDefaultModelTransform(): ModelTransform
  cloneModelTransform(rawTransform?: Record<string, unknown>): ModelTransform
  modelWorldTransform(model: Record<string, unknown> | null): ModelTransform
  modelPose(model: Record<string, unknown> | null): ModelPose
  modelLocalPointToWorld(model: Record<string, unknown> | null, point: unknown): NumericPoint | null
  modelLocalDirectionToWorld(model: Record<string, unknown> | null, direction: unknown): NumericPoint | null
  equipmentPortFrame(model: Record<string, unknown> | null, kind: string, portType?: string, end?: string): EquipmentPortFrame | null
}
declare const kernel: Readonly<ModelPosePorts>
export default kernel
