import '../../../shared/vendor/polygon-clipping.umd.js'
import '../../../shared/vendor/earcut.umd.js'
import '../../../shared/js/stl_naming.js'
import '../../../shared/js/box_geometry_product.js'
import type { BoxGeometryProductKernel } from '../../../shared/js/box_geometry_product.js'

type KernelGlobal = typeof globalThis & { boxGeometryProduct?: Readonly<BoxGeometryProductKernel> }
const kernel = (globalThis as KernelGlobal).boxGeometryProduct
if (!kernel) throw new Error('box_geometry_product.js is not loaded')
export const geometryProductKernel: Readonly<BoxGeometryProductKernel> = kernel
export type { GeometryProduct, StructuralPart } from '../../../shared/js/box_geometry_product.js'
