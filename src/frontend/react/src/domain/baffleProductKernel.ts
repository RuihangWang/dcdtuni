// The recipes read every surface from textures/materials.json.
import '../legacy/materialManifest'
import './geometryProductKernel'
import './materialRecipeKernel'
import '../../../shared/js/baffle_geometry_product.js'
import '../../../shared/js/baffle_material_recipe.js'
import type {BaffleGeometryProductKernel} from '../../../shared/js/baffle_geometry_product.js'
import type {BaffleMaterialRecipeKernel} from '../../../shared/js/baffle_material_recipe.js'
type Kernels = typeof globalThis & {baffleGeometryProduct?:BaffleGeometryProductKernel;baffleMaterialRecipe?:BaffleMaterialRecipeKernel}
const kernels=globalThis as Kernels
if(!kernels.baffleGeometryProduct || !kernels.baffleMaterialRecipe)throw new Error('Baffle product kernels are not loaded')
export const baffleGeometryProductKernel=kernels.baffleGeometryProduct
export const baffleMaterialRecipeKernel=kernels.baffleMaterialRecipe
