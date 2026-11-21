import type {StructuralPart} from './box_geometry_product.js'
import type {MaterialContext,MaterialPolicy} from './box_material_recipe.js'
export interface BaffleMaterialContext extends MaterialContext {hasDiffuseMap?:boolean}
export interface BaffleMaterialRecipe {
 schema:'dcdtuni.material-recipe/v1';kind:'baffle'
 source:{ownerUid:string;partId:string;faceId:string;patchIds:string[]}
 options:Record<string,unknown>;policy:MaterialPolicy
}
export interface BaffleMaterialRecipeKernel {
 preset(kind:string):{metalness:number;roughness:number;normalScale:number;tileM:number|null;set:string|null;baseColor:string;uv:string;doubleSide:boolean;opacity:number}
 defaultUv(kind:string):{type:'stretch'}|null
 materialPolicy(kind:string,options?:Record<string,unknown>,context?:BaffleMaterialContext):MaterialPolicy
 createRecipe(source:Record<string,unknown>,part:StructuralPart,context?:BaffleMaterialContext):BaffleMaterialRecipe
}
