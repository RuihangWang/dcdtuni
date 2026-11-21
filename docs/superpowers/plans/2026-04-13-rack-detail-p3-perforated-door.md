# Rack Detail P3: Perforated Door + Leakage Interaction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional perforated doors to rack inlet/outlet faces with hexagonal visual pattern, click-through rendering, and porousBafflePressure BC via the createBaffles pipeline. Also handle leakage σ=1.0 as no-source-term.

**Architecture:** Frontend: state properties + UI controls + hex shader + door mesh rendering + STL export + cfd_export. Backend: createBafflesDict entries + bridge BC registration + leakage σ=1.0 skip.

**Tech Stack:** Three.js r128 (ShaderMaterial), vanilla JS (STL_generate.html), Python (stlgen_runner.py).

---

### Task 1: Add door state properties and validation

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `getDefaultRackState()` (~line 1952)
  - `cloneRackState()` (~line 1972)
  - `syncRackModelState()` (~line 2022)

- [ ] **Step 1: Add defaults to `getDefaultRackState()`**

After `outletOffset: 0,` add:

```javascript
            inletDoor: null,
            outletDoor: null,
```

- [ ] **Step 2: Add to `cloneRackState()`**

After the `outletOffset` clone line, add:

```javascript
            inletDoor: rawRack.inletDoor ? { porosity: Number.isFinite(rawRack.inletDoor.porosity) ? rawRack.inletDoor.porosity : 0.5 } : null,
            outletDoor: rawRack.outletDoor ? { porosity: Number.isFinite(rawRack.outletDoor.porosity) ? rawRack.outletDoor.porosity : 0.5 } : null,
```

- [ ] **Step 3: Add validation to `syncRackModelState()`**

After the offset validation block, add:

```javascript
        // Door porosity validation
        if (rack.inletDoor) {
            rack.inletDoor.porosity = Math.min(1.0, Math.max(0.01, Number(rack.inletDoor.porosity) || 0.5));
        }
        if (rack.outletDoor) {
            rack.outletDoor.porosity = Math.min(1.0, Math.max(0.01, Number(rack.outletDoor.porosity) || 0.5));
        }
```

- [ ] **Step 4: Commit**

```bash
cd STLGeneratorForOpenFoam
git add web/STL_generate.html
git commit -m "feat(rack-detail-p3): add inletDoor/outletDoor state and validation"
```

---

### Task 2: Add hexagonal pattern shader material

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (near existing shader materials, ~line 16700)

- [ ] **Step 1: Add the hex door shader material factory**

Find the post-processing shader materials section (search for `_ppFieldShaderMaterial`). Insert BEFORE it:

```javascript
    // Hexagonal perforated door shader — procedural hex grid pattern
    function _rackDoorShaderMaterial(opacity) {
        return new THREE.ShaderMaterial({
            uniforms: {
                uOpacity: { value: opacity || 0.4 },
                uColor: { value: new THREE.Color(0.55, 0.58, 0.62) },
                uHoleFraction: { value: 0.5 },
            },
            vertexShader: [
                'varying vec2 vUv;',
                'void main() {',
                '    vUv = uv;',
                '    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);',
                '}',
            ].join('\n'),
            fragmentShader: [
                'uniform float uOpacity;',
                'uniform vec3 uColor;',
                'uniform float uHoleFraction;',
                'varying vec2 vUv;',
                '',
                'void main() {',
                '    // Hex grid tiling',
                '    vec2 uv = vUv * vec2(16.0, 28.0);',
                '    float row = floor(uv.y);',
                '    vec2 cell = vec2(uv.x + mod(row, 2.0) * 0.5, uv.y);',
                '    vec2 f = fract(cell) - 0.5;',
                '    // Hex distance (pointy-top)',
                '    vec2 af = abs(f);',
                '    float d = max(dot(af, vec2(1.0, 1.732) * 0.5), af.x);',
                '    float edge = smoothstep(uHoleFraction * 0.45, uHoleFraction * 0.45 + 0.03, d);',
                '    if (edge < 0.01) discard; // fully transparent hole',
                '    gl_FragColor = vec4(uColor, edge * uOpacity);',
                '}',
            ].join('\n'),
            side: THREE.DoubleSide,
            transparent: true,
            depthWrite: false,
        });
    }
```

- [ ] **Step 2: Verify page loads**

Refresh browser, check console — no JS errors.

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p3): add hexagonal door shader material"
```

---

### Task 3: Render door meshes in renderRackModel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - Inside `renderRackModel()`, after the cavity wall rendering (Task 5 from P2)

- [ ] **Step 1: Add door mesh rendering**

At the END of `renderRackModel()`, before the closing `}` of the function, add:

```javascript
        // P3: Perforated door rendering
        if (rack.inletDoor && centralFrameHeight > 1e-6) {
            var doorMat = _rackDoorShaderMaterial(0.4);
            var doorGeo = new THREE.PlaneGeometry(slotWidth, centralFrameHeight);
            var doorMesh = new THREE.Mesh(doorGeo, doorMat);
            // Position on inlet face (Y=0), centered on the channel
            doorMesh.position.set(slotX + slotWidth / 2, 0, safeBottomHeight + centralFrameHeight / 2);
            doorMesh.rotation.x = Math.PI / 2;
            doorMesh.layers.set(2); // click-through: raycaster ignores layer 2
            doorMesh.renderOrder = 50;
            doorMesh.userData = { modelId: model.id, isDoor: true, doorSide: 'inlet',
                                  exportName: `${rName}_inlet_door` };
            modelGroup.add(doorMesh);
        }
        if (rack.outletDoor && centralFrameHeight > 1e-6) {
            var doorMat2 = _rackDoorShaderMaterial(0.4);
            var doorGeo2 = new THREE.PlaneGeometry(slotWidth, centralFrameHeight);
            var doorMesh2 = new THREE.Mesh(doorGeo2, doorMat2);
            // Position on outlet face (Y=D), centered on the channel
            doorMesh2.position.set(slotX + slotWidth / 2, model.base.D, safeBottomHeight + centralFrameHeight / 2);
            doorMesh2.rotation.x = -Math.PI / 2;
            doorMesh2.layers.set(2); // click-through
            doorMesh2.renderOrder = 50;
            doorMesh2.userData = { modelId: model.id, isDoor: true, doorSide: 'outlet',
                                   exportName: `${rName}_outlet_door` };
            modelGroup.add(doorMesh2);
        }
```

Note: The PlaneGeometry is created in XZ plane, then rotated to face along Y axis. The rotation values may need adjustment based on the rack's local coordinate system. The implementer should visually verify the door appears on the correct face.

- [ ] **Step 2: Test in browser**

1. Open rack detail → enable inlet door via state hack: `getModelById('m_2').rack.inletDoor = {porosity: 0.5}; rebuildGeometry();`
2. Verify hex pattern mesh appears on inlet face
3. Verify clicking the door selects the U-slot behind it (click-through)

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p3): render perforated door with hex pattern and click-through"
```

---

### Task 4: Add door UI controls in Rack Detail panel

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `_renderRackDetailPanel()` (~line 8254)
  - Handler functions (near other rack detail handlers)

- [ ] **Step 1: Replace the P3 placeholder**

In `_renderRackDetailPanel()`, find:
```html
<div class="prop-group" style="border-color:#555;">
    <div class="prop-title" style="color:#888;">─── 穿孔门 / Perforated Door ───</div>
    <div style="padding:8px; color:#666; font-size:11px; text-align:center;" id="rack-detail-door">Phase 3 — 即将推出</div>
</div>
```

Replace the ENTIRE block with:

```javascript
            <div class="prop-group" style="border-color:#c06030;">
                <div class="prop-title" style="color:#c06030;">🚪 穿孔门 / Perforated Door</div>
                <div style="padding:4px 8px;">
                    <div style="display:flex; align-items:center; gap:6px; margin-bottom:6px;">
                        <label style="font-size:11px; color:#4af; min-width:60px;">Inlet 门</label>
                        <input type="checkbox" ${rack.inletDoor ? 'checked' : ''}
                               onchange="window._rdToggleDoor('${model.id}', 'inlet', this.checked)">
                        ${rack.inletDoor ? `
                        <input type="range" min="1" max="100" step="1"
                               value="${Math.round((rack.inletDoor.porosity || 0.5) * 100)}"
                               oninput="window._rdSetDoorPorosity('${model.id}', 'inlet', parseFloat(this.value))"
                               onchange="window._rdCheckpointPushed = false"
                               style="flex:1;">
                        <span style="font-size:10px; color:#666; font-family:monospace; min-width:30px;">${Math.round((rack.inletDoor.porosity || 0.5) * 100)}%</span>
                        ` : '<span style="font-size:10px; color:#555;">未启用</span>'}
                    </div>
                    <div style="display:flex; align-items:center; gap:6px;">
                        <label style="font-size:11px; color:#f64; min-width:60px;">Outlet 门</label>
                        <input type="checkbox" ${rack.outletDoor ? 'checked' : ''}
                               onchange="window._rdToggleDoor('${model.id}', 'outlet', this.checked)">
                        ${rack.outletDoor ? `
                        <input type="range" min="1" max="100" step="1"
                               value="${Math.round((rack.outletDoor.porosity || 0.5) * 100)}"
                               oninput="window._rdSetDoorPorosity('${model.id}', 'outlet', parseFloat(this.value))"
                               onchange="window._rdCheckpointPushed = false"
                               style="flex:1;">
                        <span style="font-size:10px; color:#666; font-family:monospace; min-width:30px;">${Math.round((rack.outletDoor.porosity || 0.5) * 100)}%</span>
                        ` : '<span style="font-size:10px; color:#555;">未启用</span>'}
                    </div>
                </div>
            </div>
```

- [ ] **Step 2: Add handler functions**

Near the other rack detail handlers (`_rdSetInnerWidth`, etc.), add:

```javascript
    window._rdToggleDoor = function(modelId, side, enabled) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        pushUndoCheckpoint('rackDetailDoor');
        var key = side === 'inlet' ? 'inletDoor' : 'outletDoor';
        model.rack[key] = enabled ? { porosity: 0.5 } : null;
        syncRackModelState(model);
        rebuildGeometry();
        logOperation('rackDetailDoor', (enabled ? 'Enable' : 'Disable') + ' ' + side + ' door', { modelId: modelId, side: side }, 'modify');
    };

    window._rdSetDoorPorosity = function(modelId, side, pct) {
        var model = getModelById(modelId);
        if (!model || model.kind !== 'rack') return;
        if (!window._rdCheckpointPushed) { pushUndoCheckpoint('rackDetailDoorPorosity'); window._rdCheckpointPushed = true; }
        var key = side === 'inlet' ? 'inletDoor' : 'outletDoor';
        if (model.rack[key]) {
            model.rack[key].porosity = Math.min(1.0, Math.max(0.01, pct / 100));
        }
        syncRackModelState(model);
        rebuildGeometry();
    };
```

- [ ] **Step 3: Test in browser**

1. Enter rack detail → see door section with inlet/outlet checkboxes
2. Enable inlet door → hex mesh appears, slider shows 50%
3. Change porosity → slider updates
4. Disable door → hex mesh disappears

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p3): add door toggle + porosity controls in detail panel"
```

---

### Task 5: Export door in STL and cfd_export.json

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html`
  - `buildCfdExportJson()` (~line 24543)

- [ ] **Step 1: Add door fields to cfd_export**

In `buildCfdExportJson()`, find where `rackExportObj` is built (~line 24568). After `wallBaffleSolids,` add:

```javascript
                    inletDoor: rack.inletDoor ? {
                        enabled: true,
                        porosity: rack.inletDoor.porosity,
                        solidName: sanitizeName(model.name) + '_inlet_door'
                    } : null,
                    outletDoor: rack.outletDoor ? {
                        enabled: true,
                        porosity: rack.outletDoor.porosity,
                        solidName: sanitizeName(model.name) + '_outlet_door'
                    } : null,
```

- [ ] **Step 2: Verify STL export includes door solids**

The door meshes already have `userData.exportName` set (from Task 3). The existing `exportBoxModel()` groups meshes by `userData.exportName`, so door solids will automatically appear in the STL export as `{rackName}_inlet_door` / `{rackName}_outlet_door`.

Verify: Create rack with door → export STL → search for `solid {rackName}_inlet_door` in the export.

- [ ] **Step 3: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(rack-detail-p3): export door data in cfd_export and STL"
```

---

### Task 6: Backend — createBafflesDict + bridge BC for doors

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`
  - `_build_bridge_bc()` (~line 1798)
  - `_generate_create_baffles_dict()` (~line 2793)
  - `_build_snappy_hex_mesh_dict()` (~line 2506) — add door surfaces as faceZone internal

- [ ] **Step 1: Register door BCs in `_build_bridge_bc()`**

After the existing baffle registration block (~line 2037), add:

```python
        # Perforated doors on rack inlet/outlet faces
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door_key = f"{side}Door"
                door = rack.get(door_key)
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                porosity = float(door.get("porosity", 0.5))
                registry.register(f"{solid}_master",
                                 BCSpec(bc_type="perforated_baffle", sigma=porosity))
                registry.register(f"{solid}_slave",
                                 BCSpec(bc_type="perforated_baffle", sigma=porosity))
```

- [ ] **Step 2: Add door entries to createBafflesDict**

In `_generate_create_baffles_dict()` (~line 2793), find where `baffle_configs` list is built. After it, add door configs:

```python
        # Add rack perforated door configs
        for rack_name, rack in data.get("racks", {}).items():
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{_sanitize(rack_name)}_{side}_door"))
                baffle_configs.append({
                    "zone_name": solid,
                    "porosity": float(door.get("porosity", 0.5)),
                })
```

The existing template loop at line ~2895 already generates cyclic master/slave entries for everything in `baffle_configs`.

- [ ] **Step 3: Add door surface to snappyHexMeshDict**

In `_build_snappy_hex_mesh_dict()`, find where baffle solids in `merged_rack.stl` are handled. The door solid needs `faceType internal` + `faceZone` in the refinement surfaces.

Search for where rack wall-baffle solids are added. After that section, add:

```python
        # Rack perforated door surfaces → faceType internal
        for rack_name, rack in self._data.get("racks", {}).items():
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{_sanitize(rack_name)}_{side}_door"))
                solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack[0]} {rl_rack[1]});
                faceType internal;
                faceZone {solid};
            }}""")
```

- [ ] **Step 4: Run existing tests to verify no regressions**

```bash
cd /mnt/lv/litianyu/mycode/dcdtuni
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ tests/test_liquid/ -x -q
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack-detail-p3): backend createBaffles + bridge BC for perforated doors"
```

---

### Task 7: Leakage σ=1.0 → skip fvOptions source term (P4)

**Files:**
- Modify: `src/backend/aircooling/runner/stlgen_runner.py`
  - `_build_leakage_fvoptions_config()` (~line 901)

- [ ] **Step 1: Add σ=1.0 skip**

In `_build_leakage_fvoptions_config()`, find line ~928:

```python
            sigma = spec.sigma if spec.sigma is not None else 0.1
```

Add after it:

```python
            # σ ≥ 1.0 means fully open — no resistance, skip fvOptions entry
            if sigma >= 1.0:
                logger.info(f"Leakage zone {solid_name}: σ={sigma:.2f} ≥ 1.0, skipping fvOptions (no resistance)")
                continue
```

- [ ] **Step 2: Run tests**

```bash
conda run -n dcdtuni python -m pytest src/backend/aircooling/runner/tests/ tests/test_liquid/ -x -q
```

- [ ] **Step 3: Commit**

```bash
git add src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack-detail-p4): skip leakage fvOptions when σ ≥ 1.0 (no resistance)"
```

---

### Task 8: End-to-end verification

- [ ] **Step 1: Frontend flow**

1. Create room + rack → enter rack detail
2. Enable inlet door → hex mesh appears on inlet face
3. Set porosity to 70% → slider updates
4. Click on door mesh → click passes through to U-slot (click-through works)
5. Enable outlet door too → both doors visible
6. Exit detail → doors visible in normal 3D view too
7. Export STL → `{rackName}_inlet_door` and `{rackName}_outlet_door` solids present
8. cfd_export → `inletDoor` and `outletDoor` fields with enabled/porosity/solidName
9. Disable door → hex mesh gone, cfd_export shows null

- [ ] **Step 2: Backend flow (with GTC or test case)**

1. Prepare case with rack that has a door
2. Verify createBafflesDict contains door faceZone entries
3. Verify snappyHexMeshDict has door surface with faceType internal
4. Verify bridge BC registry has `{doorSolid}_master` and `_slave` as perforated_baffle

- [ ] **Step 3: Leakage interaction**

1. Create rack → delete U-slots on both sides → leakage zone appears
2. Set leakage σ to 1.0 (100%) → verify no fvOptions entry generated
3. Add inlet door alongside leakage → both coexist

- [ ] **Step 4: Backward compatibility**

1. Load old project without doors → no errors, no doors
2. Undo/redo → door toggle reverts correctly

- [ ] **Step 5: Final commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html src/backend/aircooling/runner/stlgen_runner.py
git commit -m "feat(rack-detail-p3): complete — perforated doors + leakage interaction"
```

---

## Self-Review

**Spec coverage:**
- §1 State Properties → Task 1 ✓
- §2 UI Controls → Task 4 ✓
- §3 3D Rendering (hex shader, click-through) → Tasks 2, 3 ✓
- §4 STL Export → Task 5 ✓
- §5 cfd_export.json → Task 5 ✓
- §6 Backend (createBaffles, bridge BC, snappyHexMesh) → Task 6 ✓
- §7 P4 Leakage σ=1.0 → Task 7 ✓
- §7.2 Door + leakage coexistence → Task 8 step 3 ✓
- §9 Test Plan → Task 8 ✓

**Placeholder scan:** All code blocks complete. No TBD/TODO.

**Type consistency:** `inletDoor`/`outletDoor` (object or null), `porosity` (0.01-1.0), `solidName` (`{rackName}_{side}_door`), `bc_type="perforated_baffle"` — all consistent across tasks.
