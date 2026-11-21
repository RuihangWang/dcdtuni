// Compute BVH for all mesh geometries in a group.
// Call after buildGeometryData() completes.
window.computeSceneBVH = function(rootGroup) {
  rootGroup.traverse(function(obj) {
    if (obj.isMesh && obj.geometry && !obj.geometry.boundsTree) {
      try {
        obj.geometry.computeBoundsTree();
      } catch (e) {
        console.warn('[BVH] Failed for', obj.name, e.message);
      }
    }
  });
};

window.disposeMeshBVH = function(mesh) {
  if (mesh && mesh.geometry && mesh.geometry.boundsTree) {
    mesh.geometry.disposeBoundsTree();
  }
};
