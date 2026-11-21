import '../../../shared/js/box_material_recipe.js'
import type { BoxMaterialRecipeKernel } from '../../../shared/js/box_material_recipe.js'
type KernelGlobal = typeof globalThis & { boxMaterialRecipe?: Readonly<BoxMaterialRecipeKernel> }
const kernel = (globalThis as KernelGlobal).boxMaterialRecipe
if (!kernel) throw new Error('box_material_recipe.js is not loaded')
export const materialRecipeKernel: Readonly<BoxMaterialRecipeKernel> = kernel
export type { MaterialRecipe, MaterialContext, MaterialPolicy } from '../../../shared/js/box_material_recipe.js'
