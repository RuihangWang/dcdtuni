/** Baffle appearance intent only. Texture IO/readiness and display caches stay in the carrier. */
(function(root,factory){
 var box=root && root.boxMaterialRecipe;
 if(typeof module==='object' && module.exports)box=box || require('./box_material_recipe.js');
 var api=Object.freeze(factory(box));
 if(typeof module==='object' && module.exports)module.exports=api;
 if(root)root.baffleMaterialRecipe=api;
 if(typeof window!=='undefined')window.baffleMaterialRecipe=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(box){
 'use strict';
 if(!box)throw new Error('baffle_material_recipe.js needs box material helpers');
 function preset(kind){
  if(kind==='baffle')return {metalness:0.15,roughness:0.75,normalScale:0.4,envMapIntensity:0.2,tintColor:0x8a9098,tileM:0.25};
  if(kind==='baffle-grille')return {metalness:0.3,roughness:0.55,normalScale:0,envMapIntensity:0.25,tintColor:0xffffff,tileM:0.3};
  throw new Error('Unsupported Baffle material kind: '+kind);
 }
 function materialPolicy(kind,options,context){
  options=options || {};context=context || {};
  if(kind.indexOf('face-image:')===0)return box.materialPolicy(kind,options,context);
  var p=preset(kind),opacity=options.opacity ?? 1;
  var transparent=typeof options.transparent==='boolean'?options.transparent:opacity<0.999;
  var parameters={...(options.color!==undefined?{color:options.color}:{}),
   shininess:Math.round((1-p.roughness)*80)+10,specular:0x111111,
   side:options.doubleSide || kind==='baffle'?'double':'front',polygonOffset:true,
   polygonOffsetFactor:options.polygonOffsetFactor ?? 1,transparent:transparent,opacity:opacity,
   depthWrite:options.depthWrite ?? !transparent};
  if(context.hasDiffuseMap && !options.keepUserColor)parameters.color=p.tintColor;
  var uv=options.uvNormalize?{...options.uvNormalize}:null;
  return {model:'phong',objectKind:kind,parameters:parameters,
   pbr:{roughness:p.roughness,metalness:p.metalness,normalScale:p.normalScale},
   texture:{source:null,repeat:box.textureRepeat(options,{...context,tileSize:context.tileSize ?? p.tileM})},
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
  var kind=image?'face-image:'+image:part.isBaseWall?'baffle':'baffle-grille';
  var options={color:image || !part.isBaseWall?'#ffffff':part.materialRecipe.color,
   opacity:part.materialRecipe.opacity,doubleSide:true,objectKind:kind,
   ...(image?{uvNormalize:{type:'stretch'}}:{}),...(!part.isBaseWall?{renderOrder:3}:{})};
  // spawnRenderable's shared overlay normalization is also an explicit creation intent.
  Object.assign(options,box.overlayDefaults(options));
  return {schema:'dcdtuni.material-recipe/v1',kind:'baffle',
   source:{ownerUid:source.uid,partId:part.partId,faceId:part.faceId,patchIds:(part.refIds || []).slice()},
   options:options,policy:materialPolicy(kind,options,context)};
 }
 return {preset:preset,materialPolicy:materialPolicy,createRecipe:createRecipe};
});
