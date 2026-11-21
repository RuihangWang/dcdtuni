import type * as THREE from 'three'

/** Result colours must not mutate materials shared by unrelated equipment. */
export class MeshColorOverlay {
  private originals=new Map<THREE.Mesh,THREE.Material|THREE.Material[]>()
  get size() {return this.originals.size}
  /** The material this overlay replaced, if it painted the mesh. */
  original(mesh:THREE.Mesh) {return this.originals.get(mesh)}
  paint(mesh:THREE.Mesh,color:number) {
    if(!this.originals.has(mesh)) {
      this.originals.set(mesh,mesh.material)
      mesh.material=Array.isArray(mesh.material)?mesh.material.map(m=>m.clone()):mesh.material.clone()
    }
    for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material]) {
      const colored=material as THREE.Material & {color?:THREE.Color}
      colored.color?.setHex(color)
    }
  }
  restore() {
    for(const [mesh,original] of this.originals) {
      for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material])material.dispose()
      mesh.material=original
    }
    this.originals.clear()
  }
}
