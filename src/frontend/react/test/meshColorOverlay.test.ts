import {expect,it} from 'vitest'
import * as THREE from 'three'
import {MeshColorOverlay} from '../src/liquid/meshColorOverlay'

it('colors two servers independently without mutating their shared engineering material',()=>{
  const material=new THREE.MeshStandardMaterial({color:0x888888})
  const a=new THREE.Mesh(new THREE.BoxGeometry(),material),b=new THREE.Mesh(new THREE.BoxGeometry(),material)
  const overlay=new MeshColorOverlay()
  overlay.paint(a,0xff0000);overlay.paint(b,0x0000ff)
  expect((a.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
  expect((b.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0x0000ff)
  expect(material.color.getHex()).toBe(0x888888)
  overlay.restore()
  expect(a.material).toBe(material);expect(b.material).toBe(material)
})

it('preserves and restores multi-material server faces',()=>{
  const faces=[new THREE.MeshPhongMaterial({color:0x777777}),new THREE.MeshStandardMaterial({color:0xaaaaaa})]
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(),faces)
  const overlay=new MeshColorOverlay();overlay.paint(mesh,0x00ff00)
  expect((mesh.material as THREE.MeshStandardMaterial[]).map(m=>m.color.getHex())).toEqual([0x00ff00,0x00ff00])
  expect(faces.map(m=>m.color.getHex())).toEqual([0x777777,0xaaaaaa])
  overlay.restore();expect(mesh.material).toBe(faces)
})

it('paints per-vertex colours on a copy of the material and takes them back on restore',()=>{
  const map=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1)
  const material=new THREE.MeshStandardMaterial({color:0x2196f3,map,transparent:true,opacity:0.85})
  const geometry=new THREE.BoxGeometry()
  const mesh=new THREE.Mesh(geometry,material)
  const overlay=new MeshColorOverlay()
  const colors=new Float32Array(geometry.getAttribute('position').count*3).fill(0.5)
  overlay.paintVertices(mesh,colors)
  const painted=mesh.material as THREE.MeshStandardMaterial
  expect(painted).not.toBe(material)
  expect(painted.vertexColors).toBe(true)
  // A colour texture would tint the result colours (and the OV snapshot refuses the pair).
  expect(painted.map).toBeNull()
  expect(painted.color.getHex()).toBe(0xffffff)
  expect(painted.opacity).toBe(0.85)
  expect(Array.from((geometry.getAttribute('color') as THREE.BufferAttribute).array.slice(0,3))).toEqual([0.5,0.5,0.5])
  expect(material.vertexColors).toBe(false)
  expect(overlay.original(mesh)).toBe(material)
  // A second frame rewrites the same attribute.
  overlay.paintVertices(mesh,new Float32Array(colors.length).fill(0.25))
  expect(mesh.material).toBe(painted)
  expect((geometry.getAttribute('color') as THREE.BufferAttribute).array[0]).toBe(0.25)
  overlay.restore()
  expect(mesh.material).toBe(material)
  expect(geometry.getAttribute('color')).toBeUndefined()
})

it('switches a mesh between a flat and a per-vertex colour',()=>{
  const material=new THREE.MeshStandardMaterial({color:0x888888})
  const geometry=new THREE.BoxGeometry()
  const mesh=new THREE.Mesh(geometry,material)
  const overlay=new MeshColorOverlay()
  overlay.paintVertices(mesh,new Float32Array(geometry.getAttribute('position').count*3).fill(1))
  overlay.paint(mesh,0xff0000)
  expect((mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(false)
  expect((mesh.material as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
  expect(geometry.getAttribute('color')).toBeUndefined()
  overlay.restore()
  expect(mesh.material).toBe(material)
})

it('repaints a mesh whose geometry the display refresh replaced',()=>{
  const material=new THREE.MeshStandardMaterial()
  const first=new THREE.BoxGeometry()
  const mesh=new THREE.Mesh(first,material)
  const overlay=new MeshColorOverlay()
  overlay.paintVertices(mesh,new Float32Array(first.getAttribute('position').count*3))
  const second=new THREE.BoxGeometry(2,2,2)
  mesh.geometry=second
  overlay.paintVertices(mesh,new Float32Array(second.getAttribute('position').count*3).fill(1))
  expect(first.getAttribute('color')).toBeUndefined()
  expect(second.getAttribute('color')).toBeDefined()
  overlay.restore()
  expect(second.getAttribute('color')).toBeUndefined()
})
