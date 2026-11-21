# Image Texture Application for Data Center Objects

**Date**: 2026-05-27
**Status**: Approved
**Approach**: UV-normalized image materials (Approach A)

## Goal

Replace procedural canvas-generated textures on 6 specific object surfaces with photographic image textures for more realistic rendering. Each texture targets a distinct mesh region that already exists as a separate Three.js mesh in the current architecture.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| U-tiling for server faces | Tile N times vertically | Real servers are modular per-U; 4U server = 4 stacked IO panels |
| Power strip assignment | Strip 1 = left, Strip 2 = right | Fixed, deterministic |
| ACU return sizing | Stretch full image | Always show the complete 2x5 chevron grid |
| Slotted grille scope | Baffle + floor-vent patches | Both represent perforated airflow surfaces |
| PBR layers | Image diffuse only | Photos have visual depth baked in; no separate normal/roughness maps |

## Texture Mapping Table

| typeKey | Image file | Wrapping | UV Mode | Target mesh |
|---------|-----------|----------|---------|-------------|
| `server-front-photo` | `textures/MISC/ServerFront_compressed.jpg` | RepeatWrapping (Y) | tile-per-u | Server inlet face |
| `server-rear-socket` | `textures/MISC/1U_ServerRearSocket.jpg` | RepeatWrapping (Y) | tile-per-u | Server outlet wall remainder |
| `server-outlet-mesh` | `textures/MISC/sever_rear_outletMesh.jpg` | RepeatWrapping | tile-physical (0.1m) | Server outlet patches |
| `rack-strip-left` | `textures/MISC/Power Strip 1.jpg` | ClampToEdge | stretch | Rack frame front left strip |
| `rack-strip-right` | `textures/MISC/Power Strip 2.jpg` | ClampToEdge | stretch | Rack frame front right strip |
| `acu-return-photo` | `textures/MISC/ACU Return 3 Panel.png` | ClampToEdge | stretch | ACU return patches |
| `baffle-grille` | `textures/MISC/Slotted Grille - 600mm.jpg` | RepeatWrapping | tile-physical (0.3m) | Baffle + floor-vent patches |

## Architecture

### UV Normalization in spawnRenderable()

Add `options.uvNormalize` with three modes that transform geometry UVs before material assignment:

**stretch**: Normalize all UVs to `[0,1]` based on the geometry's UV bounding box. One copy of the texture fills the entire mesh. Used by: power strips, ACU return.

**tile-per-u**: Normalize U to `[0,1]`, V to `[0, uCount]` where uCount = `equipment.spanUnits`. Tiles the texture exactly N times vertically. Texture must use RepeatWrapping on the V axis. Used by: server front, server rear socket.

**tile-physical**: Skip UV normalization; use the existing tile-size repeat logic but with a custom tile size from `uvNormalize.tileSize` instead of the default `tileSizes[objectKind]` lookup. Used by: server outlet mesh, baffle/floor-vent grille.

When `uvNormalize` is set with mode `stretch` or `tile-per-u`, skip the existing tile-size repeat calculation (lines 7168-7184) because tiling is encoded in the UV coordinates, not texture.repeat.

### Texture Loading

New `_imageTextureCache` object stores loaded `THREE.Texture` instances keyed by typeKey. A new `_preloadImageTextures()` function runs once at startup, loading each image via `THREE.TextureLoader` and registering it in `externalTextureRegistry[typeKey].diffuse`. This allows `createMaterialForType()` to pick up the image texture automatically via the existing `ext?.diffuse` path (line 2160).

```
_imageTextureCache = {
    'server-front-photo': THREE.Texture,
    'server-rear-socket': THREE.Texture,
    'server-outlet-mesh': THREE.Texture,
    'rack-strip-left': THREE.Texture,
    'rack-strip-right': THREE.Texture,
    'acu-return-photo': THREE.Texture,
    'baffle-grille': THREE.Texture,
}
```

Wrapping per texture: RepeatWrapping for tileable textures, ClampToEdge for stretch textures. All use `SRGBColorSpace` (diffuse only, no data maps).

### Material Presets

New entries in `_materialPresets`:

```javascript
'server-front-photo': { metalness: 0.3, roughness: 0.6, normalScale: 0, envMapIntensity: 0.3, tintColor: 0xffffff },
'server-rear-socket': { metalness: 0.2, roughness: 0.65, normalScale: 0, envMapIntensity: 0.25, tintColor: 0xffffff },
'server-outlet-mesh': { metalness: 0.35, roughness: 0.5, normalScale: 0, envMapIntensity: 0.3, tintColor: 0xffffff },
'rack-strip-left':    { metalness: 0.15, roughness: 0.7, normalScale: 0, envMapIntensity: 0.2, tintColor: 0xffffff },
'rack-strip-right':   { metalness: 0.15, roughness: 0.7, normalScale: 0, envMapIntensity: 0.2, tintColor: 0xffffff },
'acu-return-photo':   { metalness: 0.1, roughness: 0.6, normalScale: 0, envMapIntensity: 0.2, tintColor: 0xffffff },
'baffle-grille':      { metalness: 0.3, roughness: 0.55, normalScale: 0, envMapIntensity: 0.25, tintColor: 0xffffff },
```

All use `tintColor: 0xffffff` (white = show photo at full color) and `normalScale: 0` (no normal map — photos have baked-in depth).

### Procedural Texture Bypass

In `_generateProceduralTexture()`, image-textured typeKeys return `{ diffuse: null, normal: null }` since their texture comes from the image file via `externalTextureRegistry`, not from canvas generation.

## Integration Points

### 1. _buildSingleServerFace() (~line 7864)

Current:
```javascript
var objectKind = isInletOutlet ? 'server-front' : undefined;
```

Changed:
```javascript
var objectKind;
if (faceId === 'inlet') objectKind = 'server-front-photo';
else if (faceId === 'outlet') objectKind = 'server-rear-socket';
// top/bottom/left/right: objectKind remains undefined (no image texture)
```

For the no-patch path (`spawnPlanarRect` call at line 7900), pass:
```javascript
uvNormalize: isInletOutlet ? { type: 'tile-per-u', uCount: equipment.spanUnits } : undefined
```

### 2. _buildServerFaceWithPatches() (~lines 8044, 8094)

Derive per-part objectKind from `faceId` inside the function:

For **patch islands** (line 8044):
```javascript
var patchOK = (faceId === 'outlet') ? 'server-outlet-mesh' : objectKind;
var patchUV = (faceId === 'outlet')
    ? { type: 'tile-physical', tileSize: 0.1 }
    : { type: 'tile-per-u', uCount: equipment.spanUnits };
```

For **wall remainder** (line 8094):
```javascript
var wallOK = (faceId === 'outlet') ? 'server-rear-socket' : objectKind;
var wallUV = { type: 'tile-per-u', uCount: equipment.spanUnits };
```

### 3. buildRackFrameGeometry() (~lines 8183-8191)

Front-face left/right strips get image textures. Modify the `['front', 'back'].forEach` block:

```javascript
// Left frame strip
if (fp.slotX > 1e-6 && fp.slotAreaHeight > 1e-6) {
    var stripOK = (side === 'front') ? 'rack-strip-left' : undefined;
    var stripUV = (side === 'front') ? { type: 'stretch' } : undefined;
    spawnFaceRect(side, 0, fp.marginBottom, fp.slotX, fp.slotAreaHeight, color,
        Object.assign({ side: side }, makeUD(side + '_strip_left')), stripOK, stripUV);
}
// Right frame strip — same pattern with 'rack-strip-right'
```

The `spawnFaceRect` closure needs a new parameter for `uvNormalize` to pass through to `spawnPlanarRect` → `spawnRenderable`. Add `uvNormalize` as an optional 9th parameter.

### 4. _buildSingleBoxFace() (~line 7593)

For ACU return patches:
```javascript
if (role === 'return') {
    islandKind = 'acu-return-photo';
    // uvNormalize passed to spawnRenderable
}
```

Pass `uvNormalize: { type: 'stretch' }` in the `spawnRenderable` options for `acu-return-photo` islands.

### 5. renderPlanarModelWithPatches() (~line 7251)

For baffle and floor-vent patches, use `'baffle-grille'` as `patchObjectKind`:

Call sites:
- `renderBaffleModel()` line 7285: pass `patchObjectKind = 'baffle-grille'`
- Floor-vent rendering: pass `patchObjectKind = 'baffle-grille'`

Pass `uvNormalize: { type: 'tile-physical', tileSize: 0.3 }` for baffle-grille patches.

### 6. spawnRenderable() (~line 7167)

Add UV normalization block after mesh creation (line 7163), before the existing tile-size repeat logic (line 7168):

```javascript
// Image-texture UV normalization
if (options.uvNormalize && geom.attributes?.uv) {
    const uv = geom.attributes.uv;
    const mode = options.uvNormalize;
    if (mode.type === 'stretch' || mode.type === 'tile-per-u') {
        let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
        for (let i = 0; i < uv.count; i++) {
            const u = uv.getX(i), v = uv.getY(i);
            if (u < minU) minU = u; if (u > maxU) maxU = u;
            if (v < minV) minV = v; if (v > maxV) maxV = v;
        }
        const rangeU = (maxU - minU) || 1, rangeV = (maxV - minV) || 1;
        const scaleV = (mode.type === 'tile-per-u') ? (mode.uCount || 1) : 1;
        for (let i = 0; i < uv.count; i++) {
            uv.setX(i, (uv.getX(i) - minU) / rangeU);
            uv.setY(i, ((uv.getY(i) - minV) / rangeV) * scaleV);
        }
        uv.needsUpdate = true;
    }
    // tile-physical: falls through to existing repeat logic with custom tileSize
}

// Existing tile-size repeat logic (modified):
if (webglAvailable && material.map && geom.attributes?.uv) {
    if (options.uvNormalize?.type === 'stretch' || options.uvNormalize?.type === 'tile-per-u') {
        // UVs already encode tiling — set repeat to (1,1)
        material.map.repeat.set(1, 1);
    } else {
        // Existing behavior: compute repeat from UV range and tile size
        const tileSize = options.uvNormalize?.tileSize || tileSizes[options.objectKind] || 0.4;
        // ... existing maxU/maxV calculation and repeat.set(...)
    }
}
```

### 7. spawnPlanarRect() (~line 7666)

Pass `uvNormalize` through from options to `spawnRenderable`:
```javascript
// Already passes all options through, just ensure uvNormalize is included
```

`spawnPlanarRect` already passes `options` to `spawnRenderable`, so `uvNormalize` flows through automatically.

## What stays unchanged

- **STL export**: Uses geometry vertices, not materials or textures
- **Undo/redo**: Textures are visual-only; state serialization unaffected
- **Material cache**: Image-textured materials cached by typeKey like all others
- **Existing procedural textures**: Still used for all non-image-textured surfaces (rack sides, tops, walls, floors, etc.)
- **rebuildServerFaceForDrag()**: Calls the same `_buildSingleServerFace` / `_buildServerFaceWithPatches`, inherits changes automatically
- **2D topview editor**: Renders its own simplified floor plan, unaffected by 3D textures

## Risks

1. **Texture load timing**: Images load asynchronously. First render after page load may show procedural textures until images finish loading. Mitigated by the existing `_textureRebuildTimer` debounce in `loadExternalTexture()` which triggers a geometry rebuild after textures load.

2. **UV normalization on non-rectangular patches**: Stretch mode computes UV bounding box, which works for any convex/concave polygon. For L-shaped or complex remainder polygons with holes, the bounding box is the full face extent — texture stretches correctly across the visible area.

3. **Material cache mutation**: The existing code mutates `material.map.repeat` in `spawnRenderable()` on a cached material. For stretch/tile-per-u modes, we set repeat to (1,1) which is safe since all meshes sharing that material type use UV-encoded tiling. For tile-physical, the existing behavior persists (last-write-wins on repeat, acceptable since meshes of similar type have similar dimensions).
