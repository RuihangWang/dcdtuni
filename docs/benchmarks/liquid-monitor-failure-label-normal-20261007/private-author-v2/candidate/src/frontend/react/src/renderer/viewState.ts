import * as THREE from 'three'
import type { CameraState } from './sceneSnapshot'

/** USD uses project coordinates; the editor may center them under a wrapper. */
export function restoreRenderCamera(view:CameraState,root:THREE.Object3D,camera:THREE.PerspectiveCamera,target:THREE.Vector3) {
  root.updateWorldMatrix(true,false)
  camera.position.fromArray(view.position).applyMatrix4(root.matrixWorld)
  target.fromArray(view.target).applyMatrix4(root.matrixWorld)
  camera.up.fromArray(view.up).transformDirection(root.matrixWorld)
  camera.fov=view.fov;camera.near=view.near;camera.far=view.far
  // Aspect belongs to the retained viewport, whose size may differ from video.
  camera.lookAt(target);camera.updateProjectionMatrix()
}
