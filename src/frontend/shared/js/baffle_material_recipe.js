/**
 * Baffle appearance intent only. Texture IO/readiness and display caches stay in the carrier.
 *
 * Kinds, all from textures/materials.json: `baffle` (a solid baffle or aisle
 * containment: aluminium frame round clear polycarbonate), `baffle-perforated`
 * (a baffle whose default boundary is perforated: perforated steel) and
 * `baffle-grille` (a patch on a baffle). A picture on a face or patch is the
 * BOX recipe's `face-image:`.
 */
(function(root,factory){
 function library(){
  var lib=(root && root.materialLibrary) || (typeof window!=='undefined' && window.materialLibrary) || null;
  if(!lib && typeof module==='object' && module.exports && typeof require==='function')lib=require('./material_library.js');
  if(!lib)throw new Error('baffle_material_recipe.js needs js/material_library.js loaded first');
  return lib;
 }
 var box=root && root.boxMaterialRecipe;
 if(typeof module==='object' && module.exports)box=box || require('./box_material_recipe.js');
 var api=Object.freeze(factory(box,library));
 if(typeof module==='object' && module.exports)module.exports=api;
 if(root)root.baffleMaterialRecipe=api;
 if(typeof window!=='undefined')window.baffleMaterialRecipe=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(box,library){
 'use strict';
 if(!box)throw new Error('baffle_material_recipe.js needs box material helpers');
 var KINDS=['baffle','baffle-perforated','baffle-grille'];
 function entry(kind){
  if(KINDS.indexOf(kind)<0)throw new Error('Unsupported Baffle material kind: '+kind);
  var m=library().material(kind);
  if(!m)throw new Error('materials.json has no material '+kind);
  return m;
 }
 /** The manifest surface of a baffle kind (a fresh copy). */
 function preset(kind){
  var m=entry(kind);
  return {metalness:m.metallic,roughness:m.roughness,normalScale:m.normalScale,tileM:m.tileM,set:m.set,
   baseColor:m.baseColor,uv:m.uv,doubleSide:m.doubleSide,opacity:m.opacity};
 }
 /** The UV mode the manifest declares, for a caller that did not choose one. */
 function defaultUv(kind){
  var m=entry(kind);
  return m.uv==='stretch'?{type:'stretch'}:null;
 }
 function materialPolicy(kind,options,context){
  options=options || {};context=context || {};
  if(kind.indexOf('face-image:')===0)return box.materialPolicy(kind,options,context);
  var m=entry(kind),opacity=(options.opacity ?? 1)*m.opacity;
  var set=m.set?library().set(m.set):null;
  // Translucent by nature (the manifest's opacity, or alpha in the colour map:
  // the polycarbonate of a containment panel) blends whatever the mesh asks.
  var natural=m.opacity<0.999 || !!(set && set.alphaInColor);
  var transparent=natural || (typeof options.transparent==='boolean'?options.transparent:opacity<0.999);
  var parameters={color:library().colorFor(kind,options.color,!!options.keepUserColor),
   roughness:m.roughness,metalness:m.metallic,
   side:options.doubleSide || m.doubleSide?'double':'front',polygonOffset:true,
   polygonOffsetFactor:options.polygonOffsetFactor ?? 1,transparent:transparent,opacity:opacity,
   depthWrite:options.depthWrite ?? !transparent};
  var normalize=options.uvNormalize || defaultUv(kind);
  var uv=normalize?{...normalize}:null;
  return {model:'standard',objectKind:kind,parameters:parameters,
   pbr:{roughness:m.roughness,metalness:m.metallic,normalScale:m.normalScale,set:m.set},
   texture:{source:null,repeat:box.textureRepeat({...options,uvNormalize:normalize || undefined},{...context,tileSize:context.tileSize ?? m.tileM})},
   uv:{normalize:uv,physicalScale:null},renderOrder:Number.isFinite(options.renderOrder)?options.renderOrder:0};
 }
 function createRecipe(source,part,context){
  if(!part || !part.materialRecipe)throw new Error('Baffle material recipe needs a structural part');
  context=context || {};
  var face=source.geometry && source.geometry.faces && source.geometry.faces[part.faceId],image;
  if(part.isBaseWall)image=box.imageSource(face);
  else if(Object.prototype.hasOwnProperty.call(context,'patchImageSource'))image=context.patchImageSource;
  else {
   var patch=face && (face.patches || []).find(function(p){return p && p.id===(part.refIds || [])[0];});
   image=box.imageSource(patch);
  }
  var perforated=source.cooling && source.cooling.type==='perforated';
  var kind=image?'face-image:'+image:part.isBaseWall?(perforated?'baffle-perforated':'baffle'):'baffle-grille';
  // A textured patch shows its material, not the patch's palette colour.
  var options={color:image || !part.isBaseWall?'#ffffff':part.materialRecipe.color,
   opacity:part.materialRecipe.opacity,doubleSide:true,objectKind:kind,
   ...(image?{uvNormalize:{type:'stretch'}}:!image && defaultUv(kind)?{uvNormalize:defaultUv(kind)}:{}),
   ...(!part.isBaseWall?{renderOrder:3,keepUserColor:false}:{})};
  // spawnRenderable's shared overlay normalization is also an explicit creation intent.
  Object.assign(options,box.overlayDefaults(options));
  return {schema:'dcdtuni.material-recipe/v1',kind:'baffle',
   source:{ownerUid:source.uid,partId:part.partId,faceId:part.faceId,patchIds:(part.refIds || []).slice()},
   options:options,policy:materialPolicy(kind,options,context)};
 }
 return {preset:preset,defaultUv:defaultUv,materialPolicy:materialPolicy,createRecipe:createRecipe};
});
