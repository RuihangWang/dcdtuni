# Non-Fan Equipment Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign non-fan equipment to support powered/unpowered modes with per-face (inlet/outlet) independent heat source configuration (fixed temperature or fixed power via `externalWallHeatFluxTemperature`).

**Architecture:** Frontend UI adds cascading dropdowns for device mode → per-face mode → heat type. Export logic maps each face independently to BC types. Backend adds `nonfan_heat_flux` BC type using OpenFOAM's `externalWallHeatFluxTemperature`.

**Tech Stack:** JavaScript (STL_generate.html), Python (bridge_bc.py, fields.py, stlgen_runner.py)

**Spec:** `docs/Omniverse-paraview/2026-03-29-nonfan-equipment-redesign.md`

---

### Task 1: Frontend — Rewrite `renderEquipmentCfdInspector()` for non-fan

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (lines 5997-6017)

- [ ] **Step 1: Replace the non-fan branch of `renderEquipmentCfdInspector`**

Find the function `renderEquipmentCfdInspector` (line 5997). Replace the entire `else` block (lines 6007-6016) with the new cascading UI:

```javascript
        } else {
            // Non-fan equipment: powered/unpowered + per-face settings
            const powerMode = srvCfd.powerMode || 'powered';
            const inletMode = srvCfd.inletMode || 'powered';
            const outletMode = srvCfd.outletMode || 'powered';
            const inletHeatType = srvCfd.inletHeatType || 'fixedTemp';
            const outletHeatType = srvCfd.outletHeatType || 'fixedTemp';
            const inletTemp = srvCfd.inletTemperature != null ? srvCfd.inletTemperature : (srvCfd.fixedTemperature != null ? srvCfd.fixedTemperature : 35);
            const outletTemp = srvCfd.outletTemperature != null ? srvCfd.outletTemperature : (srvCfd.fixedTemperature != null ? srvCfd.fixedTemperature : 35);
            const inletPower = srvCfd.inletPower != null ? srvCfd.inletPower : 500;
            const outletPower = srvCfd.outletPower != null ? srvCfd.outletPower : 500;
            const mid = model.id;
            const eid = equipment.id;

            let html = `<div class="prop-group" style="border-color:#16825d"><div class="prop-title" style="color:#16825d">${t('inspector.cfd_physics')}</div>`;

            // Device mode dropdown
            html += `<div class="prop-row"><label>${t('label.device_mode')}</label><select onchange="updateRackServerCfd('${mid}', '${eid}', 'powerMode', this.value); updateUI();">
                <option value="powered" ${powerMode === 'powered' ? 'selected' : ''}>${t('label.powered')}</option>
                <option value="unpowered" ${powerMode === 'unpowered' ? 'selected' : ''}>${t('label.unpowered')}</option>
            </select></div>`;

            if (powerMode === 'unpowered') {
                html += `<div style="font-size:0.78rem; color:var(--text-dim); line-height:1.55;">${t('hint.unpowered_desc')}</div>`;
            } else {
                // Inlet section
                html += `<div style="font-weight:500; margin-top:6px; color:var(--accent3, #00d4ff);">◆ Inlet (${t('label.front_face')})</div>`;
                html += `<div class="prop-row"><label>${t('label.face_status')}</label><select onchange="updateRackServerCfd('${mid}', '${eid}', 'inletMode', this.value); updateUI();">
                    <option value="powered" ${inletMode === 'powered' ? 'selected' : ''}>${t('label.powered')}</option>
                    <option value="unpowered" ${inletMode === 'unpowered' ? 'selected' : ''}>${t('label.unpowered')}</option>
                </select></div>`;
                if (inletMode === 'powered') {
                    html += `<div class="prop-row"><label>${t('label.heat_type')}</label><select onchange="updateRackServerCfd('${mid}', '${eid}', 'inletHeatType', this.value); updateUI();">
                        <option value="fixedTemp" ${inletHeatType === 'fixedTemp' ? 'selected' : ''}>${t('label.fixed_temperature')}</option>
                        <option value="fixedPower" ${inletHeatType === 'fixedPower' ? 'selected' : ''}>${t('label.fixed_power')}</option>
                    </select></div>`;
                    if (inletHeatType === 'fixedTemp') {
                        html += `<div class="prop-row"><label>${t('label.temperature_c')}</label><input type="number" step="1" value="${inletTemp}" onchange="updateRackServerCfd('${mid}', '${eid}', 'inletTemperature', parseFloat(this.value))"></div>`;
                    } else {
                        html += `<div class="prop-row"><label>${t('label.power_w')}</label><input type="number" step="50" value="${inletPower}" onchange="updateRackServerCfd('${mid}', '${eid}', 'inletPower', parseFloat(this.value))"></div>`;
                    }
                }

                // Outlet section
                html += `<div style="font-weight:500; margin-top:6px; color:var(--accent2, #ff0070);">◆ Outlet (${t('label.back_face')})</div>`;
                html += `<div class="prop-row"><label>${t('label.face_status')}</label><select onchange="updateRackServerCfd('${mid}', '${eid}', 'outletMode', this.value); updateUI();">
                    <option value="powered" ${outletMode === 'powered' ? 'selected' : ''}>${t('label.powered')}</option>
                    <option value="unpowered" ${outletMode === 'unpowered' ? 'selected' : ''}>${t('label.unpowered')}</option>
                </select></div>`;
                if (outletMode === 'powered') {
                    html += `<div class="prop-row"><label>${t('label.heat_type')}</label><select onchange="updateRackServerCfd('${mid}', '${eid}', 'outletHeatType', this.value); updateUI();">
                        <option value="fixedTemp" ${outletHeatType === 'fixedTemp' ? 'selected' : ''}>${t('label.fixed_temperature')}</option>
                        <option value="fixedPower" ${outletHeatType === 'fixedPower' ? 'selected' : ''}>${t('label.fixed_power')}</option>
                    </select></div>`;
                    if (outletHeatType === 'fixedTemp') {
                        html += `<div class="prop-row"><label>${t('label.temperature_c')}</label><input type="number" step="1" value="${outletTemp}" onchange="updateRackServerCfd('${mid}', '${eid}', 'outletTemperature', parseFloat(this.value))"></div>`;
                    } else {
                        html += `<div class="prop-row"><label>${t('label.power_w')}</label><input type="number" step="50" value="${outletPower}" onchange="updateRackServerCfd('${mid}', '${eid}', 'outletPower', parseFloat(this.value))"></div>`;
                    }
                }
            }

            html += `</div>`;
            return html;
        }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: redesign non-fan equipment CFD inspector with per-face settings"
```

---

### Task 2: Frontend — Update `buildCfdExportJson()` non-fan export

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/STL_generate.html` (lines 17315-17333)

- [ ] **Step 1: Replace the non-fan export block**

Find the `// nonFan: 4 separate solid regions` block (line 17315-17333). Replace with per-face BC logic:

```javascript
                    } else {
                        // nonFan: per-face independent settings
                        const powerMode = srvCfd.powerMode || 'powered';
                        const inletMode = powerMode === 'unpowered' ? 'unpowered' : (srvCfd.inletMode || 'powered');
                        const outletMode = powerMode === 'unpowered' ? 'unpowered' : (srvCfd.outletMode || 'powered');

                        function facePhysics(mode, heatType, temp, power) {
                            if (mode === 'unpowered') return { bcType: 'wall', temperature: null, power: null };
                            if (heatType === 'fixedPower') return { bcType: 'nonfan_heat_flux', temperature: null, power: power || 500 };
                            return { bcType: 'nonfan_fixed_temp', temperature: temp != null ? temp : 35.0, power: null };
                        }

                        const inletPhys = facePhysics(inletMode, srvCfd.inletHeatType || 'fixedTemp', srvCfd.inletTemperature, srvCfd.inletPower);
                        const outletPhys = facePhysics(outletMode, srvCfd.outletHeatType || 'fixedTemp', srvCfd.outletTemperature, srvCfd.outletPower);

                        return {
                            id: `${name}_${eqName}`,
                            type: 'nonFan',
                            startSlot: eq.startUnit,
                            spanSlots: eq.spanUnits,
                            inletSolidName: `${name}_${eqName}_front`,
                            outletSolidName: `${name}_${eqName}_back`,
                            topSolidName: `${name}_${eqName}_top`,
                            bottomSolidName: `${name}_${eqName}_bottom`,
                            physics: {
                                inlet: inletPhys,
                                outlet: outletPhys,
                            }
                        };
                    }
```

- [ ] **Step 2: Commit**

```bash
git add STLGeneratorForOpenFoam/web/STL_generate.html
git commit -m "feat: export per-face BC settings for non-fan equipment"
```

---

### Task 3: Backend — Update `stlgen_runner.py` non-fan BC registration

**Files:**
- Modify: `dctwin/dctwin/runner/stlgen_runner.py` (lines 1201-1209)

- [ ] **Step 1: Replace the non-fan BC registration block**

Find the `# nonFan — fixed temperature on all four faces` block (lines 1201-1209). Replace with per-face logic:

```python
                else:
                    # nonFan — per-face independent BC
                    phys_inlet = phys.get("inlet", {})
                    phys_outlet = phys.get("outlet", {})

                    # Helper to register a face BC
                    def _register_nonfan_face(face_key, face_phys):
                        solid_name = srv.get(face_key)
                        if not solid_name:
                            return
                        bc_type = face_phys.get("bcType", "wall")
                        if bc_type == "nonfan_fixed_temp":
                            T = face_phys.get("temperature", 35.0) or 35.0
                            registry.register(_sanitize(solid_name),
                                              BCSpec(bc_type="nonfan_fixed_temp", temperature=T))
                        elif bc_type == "nonfan_heat_flux":
                            power = face_phys.get("power", 500) or 500
                            # Compute face area for heat flux (W/m²)
                            span = srv.get("spanSlots", 1)
                            eq_height = span * 0.04445  # 1U = 44.45mm
                            rack_width = self._get_rack_width(rack_data)
                            area = eq_height * rack_width
                            hf = power / max(area, 1e-6)
                            registry.register(_sanitize(solid_name),
                                              BCSpec(bc_type="nonfan_heat_flux", heat_flux=hf))
                        else:
                            registry.register(_sanitize(solid_name), BCSpec(bc_type="wall"))

                    _register_nonfan_face("inletSolidName", phys_inlet)
                    _register_nonfan_face("outletSolidName", phys_outlet)
                    # Top and bottom are always wall
                    for face_key in ("topSolidName", "bottomSolidName"):
                        solid = srv.get(face_key)
                        if solid:
                            registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
```

- [ ] **Step 2: Add `_get_rack_width` helper method**

Add this method to the `STLGenRunner` class (near the other helper methods):

```python
    def _get_rack_width(self, rack_data: dict) -> float:
        """Get rack width in meters from rack data or cfd_export dimensions."""
        # Try to get from rack base dimensions
        base = rack_data.get("base", {})
        width = base.get("width") or base.get("w")
        if width and float(width) > 0:
            return float(width)
        return 0.6  # default rack width 600mm
```

- [ ] **Step 3: Add backward compatibility for old export format**

In the same `_register_nonfan_face` area, add a fallback for old-style exports that have flat `physics.fixedTemperature` instead of `physics.inlet/outlet`:

```python
                else:
                    # nonFan — per-face independent BC
                    phys_inlet = phys.get("inlet", {})
                    phys_outlet = phys.get("outlet", {})

                    # Backward compat: old format has flat fixedTemperature
                    if not phys_inlet and not phys_outlet:
                        T = phys.get("fixedTemperature", 35.0) or 35.0
                        for face_key in ("inletSolidName", "outletSolidName"):
                            solid = srv.get(face_key)
                            if solid:
                                registry.register(_sanitize(solid),
                                                  BCSpec(bc_type="nonfan_fixed_temp", temperature=T))
                        for face_key in ("topSolidName", "bottomSolidName"):
                            solid = srv.get(face_key)
                            if solid:
                                registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
                        continue

                    # ... new per-face logic from Step 1 ...
```

Combine Steps 1 and 3 into the final replacement block.

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/stlgen_runner.py
git commit -m "feat: per-face BC registration for non-fan equipment"
```

---

### Task 4: Backend — Add `nonfan_heat_flux` BC type in `fields.py`

**Files:**
- Modify: `dctwin/dctwin/runner/fields.py` (near line 544)
- Modify: `dctwin/dctwin/runner/bridge_bc.py` (line 22, BCSpec docstring)

- [ ] **Step 1: Add `nonfan_heat_flux` to the T field writer**

In `_write_t_field()`, find the block that handles `nonfan_fixed_temp` (line 544):

```python
if bc_type in ("acu_supply", "fixedTemperature", "nonfan_fixed_temp") and temperature is not None:
```

Add a new `elif` right after this block's closing lines for `nonfan_heat_flux`:

```python
                elif bc_type == "nonfan_heat_flux":
                    heat_flux = bc_info.get("heat_flux", 0)
                    lines.extend([
                        f"    {patch_name}",
                        "    {",
                        "        type            externalWallHeatFluxTemperature;",
                        "        mode            flux;",
                        f"        q               uniform {heat_flux};",
                        "        kappaMethod     fluidThermo;",
                        f"        value           uniform {t_ref_k};",
                        "    }",
                    ])
```

- [ ] **Step 2: Ensure U field treats `nonfan_heat_flux` as noSlip**

In `_write_u_field()`, find the block that handles noSlip BCs (line 484). Verify that `nonfan_heat_flux` falls through to the default noSlip path. If the bridge BC check handles it, the default `else` at the end of the priority chain already emits noSlip. If not, add it to the noSlip condition explicitly.

The current code at line 484 has a comment listing types that get noSlip. Add `nonfan_heat_flux` to this comment for clarity, but the code already falls through correctly.

- [ ] **Step 3: Update bridge_bc.py docstring**

In `bridge_bc.py`, find the docstring listing BC types (line 22). Add:

```python
    "nonfan_heat_flux" - noSlip U, externalWallHeatFluxTemperature T (power-based heat flux)
```

- [ ] **Step 4: Commit**

```bash
git add dctwin/dctwin/runner/fields.py dctwin/dctwin/runner/bridge_bc.py
git commit -m "feat: add nonfan_heat_flux BC type with externalWallHeatFluxTemperature"
```

---

### Task 5: i18n translations

**Files:**
- Modify: `STLGeneratorForOpenFoam/web/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/web/i18n/en-US.json`

- [ ] **Step 1: Add zh-CN translations**

Find the existing `"label.fixed_temp"` entry and add nearby:

```json
  "label.device_mode": "设备模式",
  "label.powered": "有源设备",
  "label.unpowered": "无源设备",
  "label.face_status": "状态",
  "label.front_face": "正面",
  "label.back_face": "背面",
  "label.heat_type": "热源类型",
  "label.fixed_temperature": "固定温度",
  "label.fixed_power": "固定功率",
  "label.temperature_c": "温度 (°C)",
  "hint.unpowered_desc": "无源设备：前后面均为 no-slip 壁面边界条件，不发热。等同于多 U-slot 盲板。",
```

- [ ] **Step 2: Add en-US translations**

```json
  "label.device_mode": "Device Mode",
  "label.powered": "Powered",
  "label.unpowered": "Unpowered",
  "label.face_status": "Status",
  "label.front_face": "Front",
  "label.back_face": "Back",
  "label.heat_type": "Heat Source",
  "label.fixed_temperature": "Fixed Temperature",
  "label.fixed_power": "Fixed Power",
  "label.temperature_c": "Temperature (°C)",
  "hint.unpowered_desc": "Unpowered device: both faces use no-slip wall BC. No heat generation. Equivalent to multi-U blank panel.",
```

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/web/i18n/zh-CN.json STLGeneratorForOpenFoam/web/i18n/en-US.json
git commit -m "feat: add i18n translations for non-fan equipment redesign"
```

---

### Task 6: Integration verification

- [ ] **Step 1: Start server and load a project with non-fan equipment**

```bash
fuser -k 8000/tcp 2>/dev/null
CONDA_BASE="$(conda info --base)"
"$CONDA_BASE/envs/dcdtuni/bin/python" STLGeneratorForOpenFoam/server/server.py \
  --case-dir dctwin/case/V5_CFD_Station &
```

- [ ] **Step 2: Verify UI in browser**

Open browser, create a rack, add a non-fan equipment. In the right panel:
1. Verify "设备模式" dropdown shows "有源设备" / "无源设备"
2. Select "无源设备" → inlet/outlet sections disappear
3. Select "有源设备" → inlet and outlet sections appear
4. Set inlet to "有源", heat type "固定温度", temperature 40°C
5. Set outlet to "有源", heat type "固定功率", power 800W
6. Set inlet to "无源" → temperature input disappears
7. Verify all combinations work without JS errors (check browser console)

- [ ] **Step 3: Verify export**

Click "导出 CFD 工程包". Check the exported `cfd_export.json`:
- Non-fan equipment should have `physics.inlet` and `physics.outlet` objects
- Each with `bcType`, `temperature`, `power` as appropriate

- [ ] **Step 4: Test backward compatibility**

Load an OLD project that has non-fan equipment with the legacy `fixedTemperature` format. Verify:
- The UI shows "有源设备" with both faces at the old temperature value
- Export still works correctly

- [ ] **Step 5: Kill server**

```bash
fuser -k 8000/tcp 2>/dev/null
```
