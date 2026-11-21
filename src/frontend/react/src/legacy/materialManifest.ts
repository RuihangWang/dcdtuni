/**
 * Installs the material and texture pool manifest into js/material_library.js.
 *
 * `shared/textures/materials.json` is the single source of every surface; the
 * shared builders read it through `window.materialLibrary` when they make a
 * material. React imports the JSON (it ships in the bundle, so there is no
 * start-up fetch) and installs it here — imported FIRST by
 * legacy/geometryBuilder.ts, so it is in before any builder runs. The legacy
 * page fetches the same file instead.
 */
import '../../../shared/js/material_library.js'
import manifest from '../../../shared/textures/materials.json'
import type { MaterialLibrary } from '../../../shared/js/material_library.js'

const lib = (globalThis as unknown as { materialLibrary?: MaterialLibrary }).materialLibrary
if (!lib) throw new Error('js/material_library.js did not attach to globalThis')
if (!lib.isReady()) lib.install(manifest)

export const materialLibrary: MaterialLibrary = lib
export type { LibraryMaterial, Lighting, MaterialLibrary, PatchPreset } from '../../../shared/js/material_library.js'
