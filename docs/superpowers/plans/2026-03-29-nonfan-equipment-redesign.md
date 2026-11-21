# Non-Fan Equipment Redesign Spec

## Overview

Redesign the non-fan equipment system to support powered/unpowered modes with per-face (inlet/outlet) independent heat source configuration. Each face can be independently set as powered (fixed temperature or fixed power) or unpowered (no-slip wall).

## Current State

Non-fan equipment currently has:
- A single `fixedTemperature` value applied to BOTH front and back faces
- A `fixedTempFace` selector (inlet/outlet) that only picks which face label to show
- No concept of powered vs unpowered
- No per-face independent control
- No power-based heat flux option
- `bc_type: nonfan_fixed_temp` for both faces, `wall` for top/bottom

## Data Model Changes

### Equipment CFD Object (`model.cfd.servers[]`)

Replace the current flat structure with per-face configuration:

```javascript
{
    equipmentId: "e_3",

    // Device-level mode
    powerMode: "powered",              // "powered" | "unpowered"

    // Inlet (front face) settings — only used when powerMode="powered"
    inletMode: "powered",              // "powered" | "unpowered"
    inletHeatType: "fixedTemp",        // "fixedTemp" | "fixedPower"
    inletTemperature: 35.0,            // degrees C (used when inletHeatType="fixedTemp")
    inletPower: 500,                   // watts (used when inletHeatType="fixedPower")

    // Outlet (back face) settings — only used when powerMode="powered"
    outletMode: "powered",             // "powered" | "unpowered"
    outletHeatType: "fixedTemp",       // "fixedTemp" | "fixedPower"
    outletTemperature: 35.0,           // degrees C
    outletPower: 500,                  // watts

    // Legacy fields (kept for fan-server compatibility, unused for non-fan)
    volumeFlowRate: null,
    power: null,
}
```

### Backward Compatibility

When loading old projects that have the legacy `fixedTemperature` / `fixedTempFace` fields:
- `powerMode` defaults to `"powered"`
- `inletMode` and `outletMode` default to `"powered"`
- `inletHeatType` and `outletHeatType` default to `"fixedTemp"`
- `inletTemperature` and `outletTemperature` default to `fixedTemperature` value (or 35.0)
- `inletPower` and `outletPower` default to 500

## UI Design (Right Inspector Panel)

When a non-fan equipment is selected, the "CFD Physics" section renders:

```
┌─────────────────────────────────┐
│ ⚡ CFD 物理设置                  │
│                                 │
│ 设备模式:  [ 有源设备 ▼ ]        │
│            options: 有源设备 / 无源设备
│                                 │
│ ─── (below only when 有源) ──── │
│                                 │
│ ◆ Inlet (正面)                  │
│   状态:     [ 有源 ▼ ]          │
│   热源类型:  [ 固定温度 ▼ ]      │
│   温度 (°C): [ 35.0 ]          │
│                                 │
│ ◆ Outlet (背面)                 │
│   状态:     [ 有源 ▼ ]          │
│   热源类型:  [ 固定功率 ▼ ]      │
│   功率 (W):  [ 500 ]           │
│                                 │
└─────────────────────────────────┘
```

### Cascading visibility rules:
1. `powerMode = "unpowered"` → hide ALL inlet/outlet settings
2. `powerMode = "powered"` → show inlet and outlet sections
3. `inletMode = "unpowered"` → hide inlet heatType/temperature/power
4. `inletMode = "powered"` → show inlet heatType dropdown
5. `inletHeatType = "fixedTemp"` → show temperature input, hide power input
6. `inletHeatType = "fixedPower"` → show power input, hide temperature input
7. Same logic for outlet

## Bridge BC Export Mapping

### Per-face BC type determination:

```
if powerMode == "unpowered":
    front_bc = "wall"
    back_bc  = "wall"
else:
    // Inlet (front)
    if inletMode == "unpowered":
        front_bc = "wall"
    elif inletHeatType == "fixedTemp":
        front_bc = "nonfan_fixed_temp"    (temperature = inletTemperature)
    elif inletHeatType == "fixedPower":
        front_bc = "nonfan_heat_flux"     (heat_flux = inletPower / inletArea)

    // Outlet (back) — same logic with outlet* fields
    ...

// Top and bottom are always "wall"
top_bc    = "wall"
bottom_bc = "wall"
```

### bridge_bc.json entries per equipment:

```json
{
    "rack_equip_front": {
        "bc_type": "nonfan_fixed_temp",
        "temperature": 35.0,
        "heat_flux": null
    },
    "rack_equip_back": {
        "bc_type": "nonfan_heat_flux",
        "temperature": null,
        "heat_flux": 250.5
    },
    "rack_equip_top": {
        "bc_type": "wall"
    },
    "rack_equip_bottom": {
        "bc_type": "wall"
    }
}
```

### Heat flux calculation:

```
heat_flux (W/m²) = power (W) / face_area (m²)
face_area = rack_width * equipment_height
equipment_height = spanUnits * 0.04445   (1U = 44.45mm)
rack_width = from rack model base dimensions
```

## Backend Changes (fields.py)

### New BC type: `nonfan_heat_flux`

In the T field writer, add handling for `nonfan_heat_flux`:

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

The U (velocity) field keeps `noSlip` for all non-fan faces regardless of heat mode — these are always solid surfaces.

### Existing BC types unchanged:
- `nonfan_fixed_temp` → `fixedValue T` (already works)
- `wall` → `noSlip U + zeroGradient T` (already works)

## Files Modified

| File | Changes |
|------|---------|
| `STL_generate.html` | Right panel CFD physics UI (cascading dropdowns) |
| `STL_generate.html` | `buildCfdExportJson()` per-face BC export logic |
| `STL_generate.html` | `updateRackServerCfd()` new fields |
| `STL_generate.html` | Backward compatibility in state restore |
| `bridge_bc.py` | Per-face BC determination + heat flux calculation |
| `fields.py` | Add `nonfan_heat_flux` → `externalWallHeatFluxTemperature` |
| `cfdproj_schema.py` | Update schema for new fields |
| `i18n/zh-CN.json` | UI labels (设备模式, 有源, 无源, 热源类型, etc.) |
| `i18n/en-US.json` | UI labels (Device Mode, Powered, Unpowered, etc.) |
