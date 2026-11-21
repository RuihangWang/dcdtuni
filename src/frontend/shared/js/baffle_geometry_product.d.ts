import type {StructuralPart} from './box_geometry_product.js'
export interface BaffleGeometryProduct {
  schema:'dcdtuni.geometry-product/v1'; modelUid:string; kind:'baffle'; parts:StructuralPart[]
}
export interface BaffleGeometryProductKernel {
  createProduct(source:Record<string,unknown>):BaffleGeometryProduct
}
