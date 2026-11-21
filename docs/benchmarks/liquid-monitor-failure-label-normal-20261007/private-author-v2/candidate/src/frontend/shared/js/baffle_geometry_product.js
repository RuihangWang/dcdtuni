/** Owned Baffle thin-sheet buffers; same polygon numerical kernel as BOX/CDU. */
(function(root,factory){
 var base=root && root.boxGeometryProduct;
 var clipping=root && root.polygonClipping;
 if(typeof module==='object' && module.exports){
  base=base || require('./box_geometry_product.js');
  clipping=clipping || require('../vendor/polygon-clipping.umd.js');
 }
 var api=Object.freeze(factory(base,clipping));
 if(typeof module==='object' && module.exports)module.exports=api;
 if(root)root.baffleGeometryProduct=api;
 if(typeof window!=='undefined')window.baffleGeometryProduct=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(base,clipping){
 'use strict';
 if(!base || typeof base.polygonBuffers!=='function' || !clipping)throw new Error('baffle_geometry_product.js needs shared polygon numerical kernel');
 function createProduct(source){
  if(!source || typeof source.uid!=='string' || !source.uid)throw new Error('Baffle geometry product needs source uid');
  var geometry=source.geometry,face=geometry && geometry.faces && geometry.faces.front,size=geometry && geometry.size;
  if(!face || !size || !Number.isFinite(size.x) || !Number.isFinite(size.y))throw new Error('Baffle geometry product needs finite source size and front face');
  var model={...source,kind:'baffle'},layout=base.getBoxFaceFrames(model).front;
  var islands=base.buildFaceIslands(model).front || [],parts=[];
  var wall=base.quadToPoly(0,0,layout.w,layout.h,geometry.cornerOffsets);
  if(Array.isArray(geometry.cutouts))geometry.cutouts.forEach(function(cutout){
   if(cutout.faceId==='front' && cutout.polygon){
    try{wall=clipping.difference(wall,cutout.polygon);}catch(e){}
   }
  });
  function part(polygon,island,islandIndex,polygonIndex,wallIndex,wallCount){
   var isWall=!island,refs=isWall?[]:island.refIds.slice();
   return {...base.polygonBuffers(polygon,layout.matrix),
    partId:model.uid+'/front/'+(isWall?'wall/'+wallIndex:'patch/'+islandIndex+'/'+polygonIndex),
    faceId:'front',exportName:base.composeModelScopedExportName(model,'front',face,isWall
      ?{isWallPart:true,partIndex:wallIndex,partCount:wallCount}
      :{isPatch:true,patchIndex:islandIndex,patchCount:islands.length}),
    refIds:refs,islandIndex:isWall?undefined:islandIndex,wallIndex:wallIndex,isBaseWall:isWall,
    materialRecipe:{role:isWall?'structural-wall':'structural-patch',uvMode:'raw',
      color:isWall?'#b08040':island.color,opacity:isWall?base.wallOpacity(face,wallIndex)
        :(face.opacity ?? (model.meta.display.opacity ?? 1)),
      source:{ownerUid:model.uid,faceId:'front',patchIds:refs.slice()}}};
  }
  islands.forEach(function(island,index){
   wall=clipping.difference(wall,island.poly);
   island.poly.forEach(function(polygon,pi){parts.push(part(polygon,island,index,pi));});
  });
  wall.forEach(function(polygon,index){parts.push(part(polygon,null,undefined,undefined,index,wall.length));});
  return {schema:'dcdtuni.geometry-product/v1',modelUid:model.uid,kind:'baffle',parts:parts};
 }
 return {createProduct:createProduct};
});
