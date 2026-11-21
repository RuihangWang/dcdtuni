import { describe,it,expect } from 'vitest'
import * as THREE from 'three'
import { restoreRenderCamera } from '../src/renderer/viewState'

describe('renderer camera round trip',()=>{
  it('returns a project-space OV view to the centered Three scene',()=>{
    const root=new THREE.Group();root.position.set(-4,0,-3);root.updateMatrixWorld(true)
    const camera=new THREE.PerspectiveCamera();const target=new THREE.Vector3()
    restoreRenderCamera({position:[8,6,7],target:[4,0,3],up:[0,1,0],fov:45,aspect:1.5,near:.1,far:1000},root,camera,target)
    expect(camera.position.toArray()).toEqual([4,6,4])
    expect(target.toArray()).toEqual([0,0,0])
    expect(camera.fov).toBe(45)
  })
})
