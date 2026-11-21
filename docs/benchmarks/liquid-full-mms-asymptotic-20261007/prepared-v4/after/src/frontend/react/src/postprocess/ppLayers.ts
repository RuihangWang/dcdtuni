/**
 * Putting post-processing results into a scene, and taking them out again.
 *
 * The layers live in their own group, separate from the model geometry. The
 * two have different lifecycles — geometry rebuilds when the project changes, a
 * result appears when a run is loaded and vanishes when it is cleared — and
 * mixing them means one rebuild wipes the other.
 *
 * ## Sets, not singletons
 *
 * Slices and streamlines are LISTS. The legacy editor lets a user add a cut at
 * 1.2 m, another at 2.4 m and a third through a hot aisle, each with its own
 * colour map and range, and the comparison between them is the whole point of
 * the feature. A single-slice model cannot express that.
 *
 * The seed sphere and the plane ring are NOT part of any set: they preview
 * where the NEXT set would come from, which is why they stay on screen while
 * the panel's numbers are being adjusted and disappear when the section is
 * switched off.
 *
 * Every object built here is disposed on replacement. A slice or a streamline
 * set is a few hundred thousand vertices; leaking one per parameter change
 * exhausts GPU memory in a session, and the symptom is a context loss that
 * looks like an unrelated crash.
 */

import * as THREE from 'three'
import type { ColorMap } from './ppColormaps'
import type { DecodedMesh, SlicePayload, StreamlineSet } from './index'
import { buildVolume, type VolumePayload } from './ppVolume'
import {
  buildAiBox,
  buildSeedSphere,
  buildSlice,
  buildSliceRing,
  buildStreamlines,
  buildSurface,
} from './ppRenderer'

export interface SliceLayer {
  id: number
  payload: SlicePayload
  visible: boolean
  colorMap: ColorMap
  clim?: [number, number]
  /** 0-1, from the set's row slider. */
  opacity: number
}

export interface StreamlineLayer {
  id: number
  set: StreamlineSet
  visible: boolean
  colorMap: ColorMap
  clim?: [number, number]
  tubeRadius: number
  opacity: number
}

export interface PostprocessLayers {
  mesh?: DecodedMesh | null
  fieldValues?: Float32Array | null
  surface: {
    visible: boolean
    colorMap: ColorMap
    climMin: number
    climMax: number
    autoScale: boolean
    hidden?: Set<string>
    /** 0-1, from the right panel's 表面场变量透明度 slider. */
    opacity?: number
  }
  slices?: SliceLayer[]
  streamlines?: StreamlineLayer[]
  /** The plane preview, tied to the panel's inputs rather than to a slice. */
  ring?: {
    show: boolean
    origin: [number, number, number]
    normal: [number, number, number]
    radius: number
  }
  /** The seed-sphere preview, tied to the panel's inputs. */
  seed?: {
    show: boolean
    centre: [number, number, number]
    radius: number
  }
  /** The AI inference region, while its panel is showing one. */
  aiBox?: {
    show: boolean
    min: [number, number, number]
    max: [number, number, number]
  }
  /** The ray-marched field volume, when one has been computed. */
  volume?: {
    payload: VolumePayload | null
    visible: boolean
    colorMap: ColorMap
    clim: [number, number]
    opacity: number
    resolution: number
  }
}

interface Disposable {
  object: THREE.Object3D
  dispose(): void
}

const built = new WeakMap<THREE.Group, Disposable[]>()
/** The live volume per group, so the render loop can feed it the camera. */
const volumeCameraSinks = new WeakMap<
  THREE.Group,
  { setCameraPosition(p: THREE.Vector3): void }
>()

/** Called from the animation loop; a no-op when no volume is drawn. */
export function updateVolumeCamera(group: THREE.Group, position: THREE.Vector3) {
  volumeCameraSinks.get(group)?.setCameraPosition(position)
}

/** Remove and dispose everything previously put in this group. */
export function clearPostprocessLayers(group: THREE.Group): void {
  for (const d of built.get(group) ?? []) {
    group.remove(d.object)
    d.dispose()
  }
  built.set(group, [])
  volumeCameraSinks.delete(group)
}

/** Push a per-set opacity into whichever material the layer ended up with. */
function setOpacity(object: THREE.Object3D, opacity: number) {
  object.traverse((o) => {
    const material = (o as THREE.Mesh).material as THREE.Material | undefined
    if (!material) return
    const shader = material as THREE.ShaderMaterial
    // The ported shaders take opacity as a uniform and discard below 0.01;
    // `material.opacity` alone would do nothing to them.
    if (shader.uniforms?.uOpacity) shader.uniforms.uOpacity.value = opacity
    material.opacity = opacity
    material.transparent = opacity < 0.999
    material.depthWrite = material.depthWrite && opacity >= 0.999
  })
}

/**
 * Rebuild the group from the current settings.
 *
 * Rebuilds wholesale rather than patching: the alternative is tracking which of
 * a dozen settings invalidates which object, and the failure mode there is a
 * stale layer that silently shows the previous field.
 */
export function applyPostprocessLayers(group: THREE.Group, layers: PostprocessLayers): void {
  clearPostprocessLayers(group)
  const created: Disposable[] = []

  if (layers.mesh && layers.surface.visible) {
    const s = buildSurface(layers.mesh, {
      values: layers.fieldValues ?? undefined,
      climMin: layers.surface.climMin,
      climMax: layers.surface.climMax,
      autoScale: layers.surface.autoScale,
      colorMap: layers.surface.colorMap,
      hidden: layers.surface.hidden,
    })
    if (layers.surface.opacity !== undefined) setOpacity(s.object, layers.surface.opacity)
    group.add(s.object)
    created.push(s)
  }

  for (const slice of layers.slices ?? []) {
    if (!slice.visible) continue
    const s = buildSlice(slice.payload, slice.colorMap, slice.clim)
    s.object.userData.ppSetId = slice.id
    setOpacity(s.object, slice.opacity)
    group.add(s.object)
    created.push(s)
  }

  for (const line of layers.streamlines ?? []) {
    if (!line.visible) continue
    const s = buildStreamlines(line.set, line.colorMap, line.clim, line.tubeRadius)
    s.object.userData.ppSetId = line.id
    setOpacity(s.object, line.opacity)
    group.add(s.object)
    created.push(s)
  }

  // The ring is independent of any cut: it previews where the plane WILL be,
  // which is what the user positions before asking for the slice.
  if (layers.ring?.show) {
    const r = buildSliceRing(layers.ring.origin, layers.ring.normal, layers.ring.radius)
    group.add(r.object)
    created.push(r)
  }

  if (layers.seed?.show) {
    const seed = buildSeedSphere(layers.seed.centre, layers.seed.radius)
    group.add(seed.object)
    created.push(seed)
  }

  if (layers.aiBox?.show) {
    const b = buildAiBox(layers.aiBox.min, layers.aiBox.max)
    group.add(b.object)
    created.push(b)
  }

  const vol = layers.volume
  if (vol?.payload && vol.visible) {
    const built = buildVolume(vol.payload, vol.colorMap, vol.clim, vol.opacity, vol.resolution)
    group.add(built.object)
    created.push(built)
    // The ray marches FROM the camera, so the uniform has to follow it every
    // frame; a volume built once and left alone renders from wherever the
    // camera was when it was built.
    volumeCameraSinks.set(group, built)
  } else {
    volumeCameraSinks.delete(group)
  }

  built.set(group, created)
}
