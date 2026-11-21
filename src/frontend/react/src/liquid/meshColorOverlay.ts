import * as THREE from 'three'

type Painted = THREE.Material & { color?: THREE.Color; vertexColors?: boolean; map?: THREE.Texture | null }

/**
 * Result colours must not mutate materials shared by unrelated equipment.
 *
 * `paint` gives a mesh one flat colour on a copy of its material.
 * `paintVertices` colours it per vertex (a pipe coloured along its length):
 * the copy turns `vertexColors` on with a white base, drops its colour
 * texture (it would tint the data colours, and the Omniverse snapshot refuses
 * vertex colours over a colour map), and the colours go into the geometry's
 * `color` attribute, which `restore` deletes again. A pipe's display refresh
 * replaces its geometry; painting it again moves the attribute to the new one.
 */
export class MeshColorOverlay {
  private originals=new Map<THREE.Mesh,THREE.Material|THREE.Material[]>()
  /** Meshes painted per vertex, and the geometry that holds their colours. */
  private vertexGeometries=new Map<THREE.Mesh,THREE.BufferGeometry>()
  get size() {return this.originals.size}
  /** The material this overlay replaced, if it painted the mesh. */
  original(mesh:THREE.Mesh) {return this.originals.get(mesh)}
  paint(mesh:THREE.Mesh,color:number) {
    if(this.vertexGeometries.has(mesh))this.restoreOne(mesh)
    if(!this.originals.has(mesh)) {
      this.originals.set(mesh,mesh.material)
      mesh.material=Array.isArray(mesh.material)?mesh.material.map(m=>m.clone()):mesh.material.clone()
    }
    for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material]) {
      const colored=material as Painted
      colored.color?.setHex(color)
    }
  }
  /** `colors`: linear RGB per vertex of the mesh's CURRENT geometry. */
  paintVertices(mesh:THREE.Mesh,colors:Float32Array) {
    if(this.originals.has(mesh)&&!this.vertexGeometries.has(mesh))this.restoreOne(mesh)
    if(!this.originals.has(mesh)) {
      this.originals.set(mesh,mesh.material)
      const copy=(m:THREE.Material)=>{
        const c=m.clone() as Painted
        c.vertexColors=true
        if('map' in c)c.map=null
        c.color?.setHex(0xffffff)
        c.needsUpdate=true
        return c
      }
      mesh.material=Array.isArray(mesh.material)?mesh.material.map(copy):copy(mesh.material)
    }
    const held=this.vertexGeometries.get(mesh)
    if(held&&held!==mesh.geometry)held.deleteAttribute('color')
    const current=mesh.geometry.getAttribute('color') as THREE.BufferAttribute|undefined
    if(held===mesh.geometry&&current&&current.array.length===colors.length) {
      ;(current.array as Float32Array).set(colors)
      current.needsUpdate=true
    } else {
      mesh.geometry.setAttribute('color',new THREE.BufferAttribute(colors,3))
    }
    this.vertexGeometries.set(mesh,mesh.geometry)
  }
  private restoreOne(mesh:THREE.Mesh) {
    const original=this.originals.get(mesh)
    if(original===undefined)return
    for(const material of Array.isArray(mesh.material)?mesh.material:[mesh.material])material.dispose()
    mesh.material=original
    this.originals.delete(mesh)
    this.vertexGeometries.get(mesh)?.deleteAttribute('color')
    this.vertexGeometries.delete(mesh)
  }
  /** Give back the material of every painted mesh not in `keep`. */
  retain(keep:ReadonlySet<THREE.Mesh>) {
    for(const mesh of [...this.originals.keys()])if(!keep.has(mesh))this.restoreOne(mesh)
  }
  restore() {
    for(const mesh of [...this.originals.keys()])this.restoreOne(mesh)
    this.originals.clear()
    this.vertexGeometries.clear()
  }
}
