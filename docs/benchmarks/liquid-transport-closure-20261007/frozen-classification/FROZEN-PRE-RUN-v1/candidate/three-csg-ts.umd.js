var ThreeCSG = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __require = /* @__PURE__ */ ((x) => typeof require !== "undefined" ? require : typeof Proxy !== "undefined" ? new Proxy(x, {
    get: (a, b) => (typeof require !== "undefined" ? require : a)[b]
  }) : x)(function(x) {
    if (typeof require !== "undefined") return require.apply(this, arguments);
    throw Error('Dynamic require of "' + x + '" is not supported');
  });
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // package/lib/esm/index.js
  var index_exports = {};
  __export(index_exports, {
    CSG: () => CSG
  });

  // package/lib/esm/CSG.js
  var import_three2 = __require("three");

  // package/lib/esm/NBuf.js
  var NBuf3 = class {
    constructor(ct) {
      this.top = 0;
      this.array = new Float32Array(ct);
    }
    write(v) {
      this.array[this.top++] = v.x;
      this.array[this.top++] = v.y;
      this.array[this.top++] = v.z;
    }
  };
  var NBuf2 = class {
    constructor(ct) {
      this.top = 0;
      this.array = new Float32Array(ct);
    }
    write(v) {
      this.array[this.top++] = v.x;
      this.array[this.top++] = v.y;
    }
  };

  // package/lib/esm/Node.js
  var Node = class _Node {
    constructor(polygons) {
      this.plane = null;
      this.front = null;
      this.back = null;
      this.polygons = [];
      if (polygons)
        this.build(polygons);
    }
    clone() {
      const node = new _Node();
      node.plane = this.plane && this.plane.clone();
      node.front = this.front && this.front.clone();
      node.back = this.back && this.back.clone();
      node.polygons = this.polygons.map((p) => p.clone());
      return node;
    }
    // Convert solid space to empty space and empty space to solid space.
    invert() {
      for (let i = 0; i < this.polygons.length; i++)
        this.polygons[i].flip();
      this.plane && this.plane.flip();
      this.front && this.front.invert();
      this.back && this.back.invert();
      const temp = this.front;
      this.front = this.back;
      this.back = temp;
    }
    // Recursively remove all polygons in `polygons` that are inside this BSP
    // tree.
    clipPolygons(polygons) {
      if (!this.plane)
        return polygons.slice();
      let front = new Array(), back = new Array();
      for (let i = 0; i < polygons.length; i++) {
        this.plane.splitPolygon(polygons[i], front, back, front, back);
      }
      if (this.front)
        front = this.front.clipPolygons(front);
      this.back ? back = this.back.clipPolygons(back) : back = [];
      return front.concat(back);
    }
    // Remove all polygons in this BSP tree that are inside the other BSP tree
    // `bsp`.
    clipTo(bsp) {
      this.polygons = bsp.clipPolygons(this.polygons);
      if (this.front)
        this.front.clipTo(bsp);
      if (this.back)
        this.back.clipTo(bsp);
    }
    // Return a list of all polygons in this BSP tree.
    allPolygons() {
      let polygons = this.polygons.slice();
      if (this.front)
        polygons = polygons.concat(this.front.allPolygons());
      if (this.back)
        polygons = polygons.concat(this.back.allPolygons());
      return polygons;
    }
    // Build a BSP tree out of `polygons`. When called on an existing tree, the
    // new polygons are filtered down to the bottom of the tree and become new
    // nodes there. Each set of polygons is partitioned using the first polygon
    // (no heuristic is used to pick a good split).
    build(polygons) {
      if (!polygons.length)
        return;
      if (!this.plane)
        this.plane = polygons[0].plane.clone();
      const front = [], back = [];
      for (let i = 0; i < polygons.length; i++) {
        this.plane.splitPolygon(polygons[i], this.polygons, this.polygons, front, back);
      }
      if (front.length) {
        if (!this.front)
          this.front = new _Node();
        this.front.build(front);
      }
      if (back.length) {
        if (!this.back)
          this.back = new _Node();
        this.back.build(back);
      }
    }
  };

  // package/lib/esm/Vector.js
  var import_three = __require("three");
  var Vector = class _Vector {
    constructor(x = 0, y = 0, z = 0) {
      this.x = x;
      this.y = y;
      this.z = z;
    }
    copy(v) {
      this.x = v.x;
      this.y = v.y;
      this.z = v.z;
      return this;
    }
    clone() {
      return new _Vector(this.x, this.y, this.z);
    }
    negate() {
      this.x *= -1;
      this.y *= -1;
      this.z *= -1;
      return this;
    }
    add(a) {
      this.x += a.x;
      this.y += a.y;
      this.z += a.z;
      return this;
    }
    sub(a) {
      this.x -= a.x;
      this.y -= a.y;
      this.z -= a.z;
      return this;
    }
    times(a) {
      this.x *= a;
      this.y *= a;
      this.z *= a;
      return this;
    }
    dividedBy(a) {
      this.x /= a;
      this.y /= a;
      this.z /= a;
      return this;
    }
    lerp(a, t) {
      return this.add(new _Vector().copy(a).sub(this).times(t));
    }
    unit() {
      return this.dividedBy(this.length());
    }
    length() {
      return Math.sqrt(Math.pow(this.x, 2) + Math.pow(this.y, 2) + Math.pow(this.z, 2));
    }
    normalize() {
      return this.unit();
    }
    cross(b) {
      const a = this.clone();
      const ax = a.x, ay = a.y, az = a.z;
      const bx = b.x, by = b.y, bz = b.z;
      this.x = ay * bz - az * by;
      this.y = az * bx - ax * bz;
      this.z = ax * by - ay * bx;
      return this;
    }
    dot(b) {
      return this.x * b.x + this.y * b.y + this.z * b.z;
    }
    toVector3() {
      return new import_three.Vector3(this.x, this.y, this.z);
    }
  };

  // package/lib/esm/Plane.js
  var Plane = class _Plane {
    constructor(normal, w) {
      this.normal = normal;
      this.w = w;
      this.normal = normal;
      this.w = w;
    }
    clone() {
      const plane = new _Plane(this.normal.clone(), this.w);
      if (this.classificationEpsilon !== void 0) plane.classificationEpsilon = this.classificationEpsilon;
      return plane;
    }
    flip() {
      this.normal.negate();
      this.w = -this.w;
    }
    // Split `polygon` by this plane if needed, then put the polygon or polygon
    // fragments in the appropriate lists. Coplanar polygons go into either
    // `coplanarFront` or `coplanarBack` depending on their orientation with
    // respect to this plane. Polygons in front or in back of this plane go into
    // either `front` or `back`.
    splitPolygon(polygon, coplanarFront, coplanarBack, front, back) {
      const COPLANAR = 0;
      const FRONT = 1;
      const BACK = 2;
      const SPANNING = 3;
      let polygonType = 0;
      const types = [];
      for (let i = 0; i < polygon.vertices.length; i++) {
        const t = this.normal.dot(polygon.vertices[i].pos) - this.w;
        const epsilon = this.classificationEpsilon === void 0 ? _Plane.EPSILON : this.classificationEpsilon;
        const type = t < -epsilon ? BACK : t > epsilon ? FRONT : COPLANAR;
        polygonType |= type;
        types.push(type);
      }
      switch (polygonType) {
        case COPLANAR:
          (this.normal.dot(polygon.plane.normal) > 0 ? coplanarFront : coplanarBack).push(polygon);
          break;
        case FRONT:
          front.push(polygon);
          break;
        case BACK:
          back.push(polygon);
          break;
        case SPANNING: {
          const f = [], b = [];
          for (let i = 0; i < polygon.vertices.length; i++) {
            const j = (i + 1) % polygon.vertices.length;
            const ti = types[i], tj = types[j];
            const vi = polygon.vertices[i], vj = polygon.vertices[j];
            if (ti != BACK)
              f.push(vi);
            if (ti != FRONT)
              b.push(ti != BACK ? vi.clone() : vi);
            if ((ti | tj) == SPANNING) {
              const t = (this.w - this.normal.dot(vi.pos)) / this.normal.dot(new Vector().copy(vj.pos).sub(vi.pos));
              const v = vi.interpolate(vj, t);
              f.push(v);
              b.push(v.clone());
            }
          }
          if (f.length >= 3)
            front.push(new Polygon(f, polygon.shared, polygon.plane.classificationEpsilon === void 0 ? void 0 : polygon.plane));
          if (b.length >= 3)
            back.push(new Polygon(b, polygon.shared, polygon.plane.classificationEpsilon === void 0 ? void 0 : polygon.plane));
          break;
        }
      }
    }
    static fromPoints(a, b, c) {
      const n = new Vector().copy(b).sub(a).cross(new Vector().copy(c).sub(a)).normalize();
      return new _Plane(n.clone(), n.dot(a));
    }
  };
  Plane.EPSILON = 1e-5;

  // package/lib/esm/Polygon.js
  var Polygon = class _Polygon {
    constructor(vertices, shared, supportPlane) {
      this.vertices = vertices;
      this.shared = shared;
      this.plane = supportPlane ? supportPlane.clone() : Plane.fromPoints(vertices[0].pos, vertices[1].pos, vertices[2].pos);
    }
    clone() {
      return new _Polygon(this.vertices.map((v) => v.clone()), this.shared, this.plane.classificationEpsilon === void 0 ? void 0 : this.plane);
    }
    flip() {
      this.vertices.reverse().map((v) => v.flip());
      this.plane.flip();
    }
  };

  // package/lib/esm/Vertex.js
  var Vertex = class _Vertex {
    constructor(pos, normal, uv, color) {
      this.pos = new Vector().copy(pos);
      this.normal = new Vector().copy(normal);
      this.uv = new Vector().copy(uv);
      this.uv.z = 0;
      color && (this.color = new Vector().copy(color));
    }
    clone() {
      return new _Vertex(this.pos, this.normal, this.uv, this.color);
    }
    // Invert all orientation-specific data (e.g. vertex normal). Called when the
    // orientation of a polygon is flipped.
    flip() {
      this.normal.negate();
    }
    // Create a new vertex between this vertex and `other` by linearly
    // interpolating all properties using a parameter of `t`. Subclasses should
    // override this to interpolate additional properties.
    interpolate(other, t) {
      return new _Vertex(this.pos.clone().lerp(other.pos, t), this.normal.clone().lerp(other.normal, t), this.uv.clone().lerp(other.uv, t), this.color && other.color && this.color.clone().lerp(other.color, t));
    }
  };

  // package/lib/esm/CSG.js
  var CSG = class _CSG {
    constructor() {
      this.polygons = new Array();
    }
    static fromPolygons(polygons) {
      const csg = new _CSG();
      csg.polygons = polygons;
      return csg;
    }
    static fromGeometry(geom, objectIndex, classificationEpsilon) {
      if (classificationEpsilon !== void 0 && (!Number.isFinite(classificationEpsilon) || classificationEpsilon <= 0 || classificationEpsilon > Plane.EPSILON))
        throw new Error("CSG classification precision must be positive and no looser than the default");
      let polys = [];
      const posattr = geom.attributes.position;
      const normalattr = geom.attributes.normal;
      const uvattr = geom.attributes.uv;
      const colorattr = geom.attributes.color;
      const grps = geom.groups;
      let index;
      if (geom.index) {
        index = geom.index.array;
      } else {
        index = new Array(posattr.array.length / posattr.itemSize | 0);
        for (let i = 0; i < index.length; i++)
          index[i] = i;
      }
      const triCount = index.length / 3 | 0;
      polys = new Array(triCount);
      for (let i = 0, pli = 0, l = index.length; i < l; i += 3, pli++) {
        const vertices = new Array(3);
        for (let j = 0; j < 3; j++) {
          const vi = index[i + j];
          const vp = vi * 3;
          const vt = vi * 2;
          const x = posattr.array[vp];
          const y = posattr.array[vp + 1];
          const z = posattr.array[vp + 2];
          const nx = normalattr.array[vp];
          const ny = normalattr.array[vp + 1];
          const nz = normalattr.array[vp + 2];
          const u = uvattr === null || uvattr === void 0 ? void 0 : uvattr.array[vt];
          const v = uvattr === null || uvattr === void 0 ? void 0 : uvattr.array[vt + 1];
          vertices[j] = new Vertex(new Vector(x, y, z), new Vector(nx, ny, nz), new Vector(u, v, 0), colorattr && new Vector(colorattr.array[vt], colorattr.array[vt + 1], colorattr.array[vt + 2]));
        }
        if (objectIndex === void 0 && grps && grps.length > 0) {
          for (const grp of grps) {
            if (i >= grp.start && i < grp.start + grp.count) {
              polys[pli] = new Polygon(vertices, grp.materialIndex);
            }
          }
        } else {
          polys[pli] = new Polygon(vertices, objectIndex);
        }
      }
      if (classificationEpsilon !== void 0) for (const p of polys) p.plane.classificationEpsilon = classificationEpsilon;
      return _CSG.fromPolygons(polys.filter((p) => !isNaN(p.plane.normal.x)));
    }
    static toGeometry(csg, toMatrix) {
      let triCount = 0;
      const ps = csg.polygons;
      for (const p of ps) {
        triCount += p.vertices.length - 2;
      }
      const geom = new import_three2.BufferGeometry();
      const vertices = new NBuf3(triCount * 3 * 3);
      const normals = new NBuf3(triCount * 3 * 3);
      const uvs = new NBuf2(triCount * 2 * 3);
      let colors;
      const grps = [];
      const dgrp = [];
      for (const p of ps) {
        const pvs = p.vertices;
        const pvlen = pvs.length;
        if (p.shared !== void 0) {
          if (!grps[p.shared])
            grps[p.shared] = [];
        }
        if (pvlen && pvs[0].color !== void 0) {
          if (!colors)
            colors = new NBuf3(triCount * 3 * 3);
        }
        for (let j = 3; j <= pvlen; j++) {
          const grp = p.shared === void 0 ? dgrp : grps[p.shared];
          grp.push(vertices.top / 3, vertices.top / 3 + 1, vertices.top / 3 + 2);
          vertices.write(pvs[0].pos);
          vertices.write(pvs[j - 2].pos);
          vertices.write(pvs[j - 1].pos);
          normals.write(pvs[0].normal);
          normals.write(pvs[j - 2].normal);
          normals.write(pvs[j - 1].normal);
          if (uvs) {
            uvs.write(pvs[0].uv);
            uvs.write(pvs[j - 2].uv);
            uvs.write(pvs[j - 1].uv);
          }
          if (colors) {
            colors.write(pvs[0].color);
            colors.write(pvs[j - 2].color);
            colors.write(pvs[j - 1].color);
          }
        }
      }
      geom.setAttribute("position", new import_three2.BufferAttribute(vertices.array, 3));
      geom.setAttribute("normal", new import_three2.BufferAttribute(normals.array, 3));
      uvs && geom.setAttribute("uv", new import_three2.BufferAttribute(uvs.array, 2));
      colors && geom.setAttribute("color", new import_three2.BufferAttribute(colors.array, 3));
      for (let gi = 0; gi < grps.length; gi++) {
        if (grps[gi] === void 0) {
          grps[gi] = [];
        }
      }
      if (grps.length) {
        let index = [];
        let gbase = 0;
        for (let gi = 0; gi < grps.length; gi++) {
          geom.addGroup(gbase, grps[gi].length, gi);
          gbase += grps[gi].length;
          index = index.concat(grps[gi]);
        }
        geom.addGroup(gbase, dgrp.length, grps.length);
        index = index.concat(dgrp);
        geom.setIndex(index);
      }
      const inv = new import_three2.Matrix4().copy(toMatrix).invert();
      geom.applyMatrix4(inv);
      geom.computeBoundingSphere();
      geom.computeBoundingBox();
      return geom;
    }
    static fromMesh(mesh, objectIndex, classificationEpsilon) {
      const csg = _CSG.fromGeometry(mesh.geometry, objectIndex, classificationEpsilon);
      const ttvv0 = new import_three2.Vector3();
      const tmpm3 = new import_three2.Matrix3();
      tmpm3.getNormalMatrix(mesh.matrix);
      for (let i = 0; i < csg.polygons.length; i++) {
        const p = csg.polygons[i];
        for (let j = 0; j < p.vertices.length; j++) {
          const v = p.vertices[j];
          v.pos.copy(ttvv0.copy(v.pos.toVector3()).applyMatrix4(mesh.matrix));
          v.normal.copy(ttvv0.copy(v.normal.toVector3()).applyMatrix3(tmpm3));
        }
        if (classificationEpsilon !== void 0) {
          p.plane = Plane.fromPoints(p.vertices[0].pos, p.vertices[1].pos, p.vertices[2].pos);
          p.plane.classificationEpsilon = classificationEpsilon;
        }
      }
      return csg;
    }
    static toMesh(csg, toMatrix, toMaterial) {
      const geom = _CSG.toGeometry(csg, toMatrix);
      const m = new import_three2.Mesh(geom, toMaterial);
      m.matrix.copy(toMatrix);
      m.matrix.decompose(m.position, m.quaternion, m.scale);
      m.rotation.setFromQuaternion(m.quaternion);
      m.updateMatrixWorld();
      m.castShadow = m.receiveShadow = true;
      return m;
    }
    static union(meshA, meshB) {
      const csgA = _CSG.fromMesh(meshA);
      const csgB = _CSG.fromMesh(meshB);
      return _CSG.toMesh(csgA.union(csgB), meshA.matrix, meshA.material);
    }
    static subtract(meshA, meshB) {
      const csgA = _CSG.fromMesh(meshA);
      const csgB = _CSG.fromMesh(meshB);
      return _CSG.toMesh(csgA.subtract(csgB), meshA.matrix, meshA.material);
    }
    static intersect(meshA, meshB) {
      const csgA = _CSG.fromMesh(meshA);
      const csgB = _CSG.fromMesh(meshB);
      return _CSG.toMesh(csgA.intersect(csgB), meshA.matrix, meshA.material);
    }
    clone() {
      const csg = new _CSG();
      csg.polygons = this.polygons.map((p) => p.clone()).filter((p) => Number.isFinite(p.plane.w));
      return csg;
    }
    toPolygons() {
      return this.polygons;
    }
    union(csg) {
      const a = new Node(this.clone().polygons);
      const b = new Node(csg.clone().polygons);
      a.clipTo(b);
      b.clipTo(a);
      b.invert();
      b.clipTo(a);
      b.invert();
      a.build(b.allPolygons());
      return _CSG.fromPolygons(a.allPolygons());
    }
    subtract(csg) {
      const a = new Node(this.clone().polygons);
      const b = new Node(csg.clone().polygons);
      a.invert();
      a.clipTo(b);
      b.clipTo(a);
      b.invert();
      b.clipTo(a);
      b.invert();
      a.build(b.allPolygons());
      a.invert();
      return _CSG.fromPolygons(a.allPolygons());
    }
    intersect(csg) {
      const a = new Node(this.clone().polygons);
      const b = new Node(csg.clone().polygons);
      a.invert();
      b.clipTo(a);
      b.invert();
      a.clipTo(b);
      b.clipTo(a);
      a.build(b.allPolygons());
      a.invert();
      return _CSG.fromPolygons(a.allPolygons());
    }
    // Return a new CSG solid with solid and empty space switched. This solid is
    // not modified.
    inverse() {
      const csg = this.clone();
      for (const p of csg.polygons) {
        p.flip();
      }
      return csg;
    }
    toMesh(toMatrix, toMaterial) {
      return _CSG.toMesh(this, toMatrix, toMaterial);
    }
    toGeometry(toMatrix) {
      return _CSG.toGeometry(this, toMatrix);
    }
  };
  return __toCommonJS(index_exports);
})();
