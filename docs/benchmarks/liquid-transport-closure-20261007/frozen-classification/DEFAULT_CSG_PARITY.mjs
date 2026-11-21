import { readFileSync,writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
const P=path.dirname(fileURLToPath(import.meta.url)),require=createRequire(path.join(P,'reviewtree/src/frontend/react/package.json')),THREE=require('three')
const load=file=>new Function('require',readFileSync(file,'utf8')+'\nreturn ThreeCSG;')(n=>{if(n!=='three')throw new Error('unexpected dependency');return THREE}).CSG
const describe=csg=>csg.polygons.map(p=>({owner:p.shared,plane:[p.plane.normal.x,p.plane.normal.y,p.plane.normal.z,p.plane.w],vertices:p.vertices.map(v=>[v.pos.x,v.pos.y,v.pos.z,v.normal.x,v.normal.y,v.normal.z]),classificationEpsilon:p.plane.classificationEpsilon}))
function output(CSG) {
 const a=new THREE.Mesh(new THREE.BoxGeometry(2,2,2)),b=new THREE.Mesh(new THREE.BoxGeometry(1.5,1.4,2.3));b.position.set(.3,.12,-.2);b.rotation.set(.23,.45,-.12);a.updateMatrix();b.updateMatrix()
 const ac=CSG.fromMesh(a,37),bc=CSG.fromMesh(b,41),box={union:describe(ac.union(bc)),subtract:describe(ac.subtract(bc)),intersect:describe(ac.intersect(bc))}
 const plate=new THREE.Mesh(new THREE.BoxGeometry(4,.2,4)),hole=new THREE.Mesh(new THREE.CylinderGeometry(.3,.3,1,16));plate.updateMatrix();hole.position.set(.2,0,-.3);hole.updateMatrix()
 return {rotatedBox:box,plateHole:describe(CSG.fromMesh(plate,37).subtract(CSG.fromMesh(hole,41)))}
}
const before=JSON.stringify(output(load(path.join(P,'before/three-csg-ts.umd.js')))),after=JSON.stringify(output(load(path.join(P,'candidate/three-csg-ts.umd.js'))))
const hash=s=>createHash('sha256').update(s).digest('hex');writeFileSync(path.join(P,'DEFAULT-CSG-BEFORE.json'),before+'\n');writeFileSync(path.join(P,'DEFAULT-CSG-AFTER.json'),after+'\n')
const result={defaultOnly:true,classificationEpsilonNotProvided:true,exactValueAndOrderEqual:before===after,beforeSha256:hash(before),afterSha256:hash(after),jsonBytes:Buffer.byteLength(before),fixtures:['rotated boxes union/subtract/intersect','plate with cylindrical hole'],accepted:before===after};writeFileSync(path.join(P,'DEFAULT-CSG-PARITY-RESULT.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));if(!result.accepted)process.exitCode=1
