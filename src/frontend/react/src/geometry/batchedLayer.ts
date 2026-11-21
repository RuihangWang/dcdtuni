import * as THREE from 'three'

/**
 * The layer a batched original sits on while the static batch draws it (geometry/staticBatch.ts). The viewport's
 * camera does not render it, so three skips the original in its frustum test, every frame: Alibaba-hongchou's
 * 19,901 culled originals were a sixth of the frame. Every raycaster sees it (below), so picking is unchanged.
 */
export const BATCHED_LAYER = 30

/**
 * Every Raycaster tests BATCHED_LAYER as well as its own layers, wherever it was made (24 places in the editor and
 * the shared scripts): `this.layers = new Layers()` in its constructor goes through this setter. Imported first by
 * main.tsx, before any module that makes one at load time.
 */
const proto = THREE.Raycaster.prototype as unknown as { __batchedLayer?: boolean }
if (!proto.__batchedLayer) {
  const store = new WeakMap<object, THREE.Layers>()
  Object.defineProperty(THREE.Raycaster.prototype, 'layers', {
    configurable: true,
    get(this: object) { return store.get(this) },
    set(this: object, layers: THREE.Layers) {
      layers.enable(BATCHED_LAYER)
      store.set(this, layers)
    },
  })
  proto.__batchedLayer = true
}
