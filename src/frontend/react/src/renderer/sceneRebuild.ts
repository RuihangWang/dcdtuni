import type { SceneBuilder } from '../interaction/sceneBuilder'

/**
 * Rebuild the live scene from the project, two animation frames after the call so React has committed the
 * latest edit (a rebuild straight after a store edit builds the previous project). Resolves to the mesh count,
 * or -1 before the viewport has mounted a builder.
 */
export async function rebuildSceneFromProject(): Promise<number> {
  await new Promise<void>((resolve) => {
    if (typeof requestAnimationFrame !== 'function') {
      setTimeout(resolve, 32)
      return
    }
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
  })
  const builder = (window as unknown as { __sceneBuilder?: SceneBuilder }).__sceneBuilder
  if (!builder) return -1
  builder.rebuild()
  return builder.registries.renderMeshes.length
}
