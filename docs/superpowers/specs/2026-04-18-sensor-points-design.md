# Sensor Points (用户手动探点) 设计文档

**Date:** 2026-04-18
**Scope:** STLGeneratorForOpenFoam 前端 + server.py + dcdtuni runner（少量）
**Goal:** 允许用户在求解 tab 手动添加任意 (x,y,z) 坐标的传感器位点，OpenFOAM `probes` FO 在求解时输出物理量；浮窗监控新增"传感器" tab 实时画温度曲线；求解完成后最终迭代步值写 JSON 到 `<case>/sensor_results/<YYYY-MM-DD>/*.json`。

## Existing Infrastructure Audit

后端、schema、模板**几乎已就位**；本次仅前端 + 两个新 server API + 一个 JSON harvester：

| 层 | 既有 | 需新增 |
|---|---|---|
| Pydantic schema `SensorPoint` | ✅ `cfdproj_schema.py:46` | — |
| Runner `_extract_sensor_points` | ✅ `stlgen_runner.py:904` | — |
| `controlDict.j2` probes 块 | ✅ 已写 `T rho p p_rgh U` 五场 | — |
| 前端 UI panel | ❌ 完全缺 | 全部 |
| `/api/probes-latest` | ❌ | 新增 |
| `/api/harvest-sensor-results` | ❌ | 新增 |
| 浮窗第 3 tab "传感器" | ❌ | 新增 |

---

## Architecture — End-to-End

```
┌─────────────────────────────────────────────────────────────────┐
│ [求解 tab] 右边栏 #solver-settings-panel                         │
│   新 prop-group "📍 传感器位点"（放在残差设置和"执行求解"之间）     │
│     ├─ 温度单位 dropdown (全局 _ppTempUnit, K/C/F, default C)    │
│     ├─ X/Y/Z 输入 + "+添加" 按钮                                │
│     ├─ list: ☑ name ✕   dblclk→editDialog                     │
│     └─ 📂 打开最近 sensor JSON 按钮                             │
│                                                                 │
│ state.sensorPoints = [{id, name, x, y, z, enabled}]            │
│   └→ buildCfdrunJson → cfdrun.sensorPoints (已有字段)           │
├─────────────────────────────────────────────────────────────────┤
│ runner 端已就位：                                                │
│   _extract_sensor_points → controlDict.j2 probes 块              │
│   输出 5 场 T/U/p/p_rgh/rho                                     │
├─────────────────────────────────────────────────────────────────┤
│ OpenFOAM 求解中持续写：                                          │
│   <case>/log/<run_name>/postProcessing/probes/<t0>/{T,U,p,...}   │
│   probes FO 在每次 writeInterval 时打印 "writing probe data"     │
│   banner 到 solver log —— 浮窗用它做 polling 触发锚               │
├─────────────────────────────────────────────────────────────────┤
│ [新] GET /api/probes-latest?since=<step>                         │
│   server 端 auto-detect 最新 run_dir → 增量解析 probes 文件 →    │
│   返回 { steps, writeInterval, fields: {T,U,p,p_rgh,rho} }       │
├─────────────────────────────────────────────────────────────────┤
│ [新] 浮窗第 3 tab "传感器"                                       │
│   数据挂 _solverMonitor.sensors[name] = [{step, value}, ...]     │
│   _smParseLogLine 抓到 "writing probe data" banner → 触发        │
│   _smSensorPoller.fetchNow() → 分发到 sensors[] → _smDrawChart   │
│   fallback: 2s timer 兜底                                       │
├─────────────────────────────────────────────────────────────────┤
│ [新] 求解结束钩子（_solverMonitor.active=false 时）               │
│   前端 POST /api/harvest-sensor-results                          │
│   server 读最后一步 probes 行 → JSON dump 到                     │
│   <case>/sensor_results/<YYYY-MM-DD>/<case>_sensor_<HH-MM-SS>.json│
└─────────────────────────────────────────────────────────────────┘
```

---

## 1 · UI — Panel in `#solver-settings-panel`

**位置：** 在"📉 收敛残差阈值设置" (`prop-group`) 和"▶ 执行求解" 按钮之间插入新 `prop-group`。

**HTML 结构（照抄 slice `prop-group` 风格）：**

```html
<div class="prop-group" style="border-color:<sensor-color>; margin:8px;">
  <div class="prop-title" style="color:<sensor-color>">📍 传感器位点</div>

  <div class="prop-row">
    <label data-i18n="sensor.temp_unit">温度显示单位</label>
    <select id="sensor-temp-unit">
      <option value="K">K</option>
      <option value="C" selected>°C</option>
      <option value="F">°F</option>
    </select>
  </div>

  <div class="prop-row" style="gap:4px;">
    <label>X</label><input type="number" id="sensor-input-x" step="0.01" value="0">
    <label>Y</label><input type="number" id="sensor-input-y" step="0.01" value="0">
    <label>Z</label><input type="number" id="sensor-input-z" step="0.01" value="0">
    <button onclick="window.addSensorPoint()">+添加</button>
  </div>

  <div id="sensor-list" style="margin-top:4px;"></div>

  <button id="sensor-open-json-btn" onclick="window.openLatestSensorJson()">
    📂 打开最近 sensor JSON
  </button>
</div>
```

**list row（`_updateSensorListUI`）：**

```
┌──────────────────────────────────┐
│ ☑  sensor_1                  ✕  │
│ ☑  CRAC_Inlet_Left           ✕  │
│ ☐  sensor_3                  ✕  │
└──────────────────────────────────┘
```

每行三件套，无瞬时值列：
- `checkbox`：控制该点是否进浮窗第 3 tab 的曲线（默认 `checked`）
- `name` span：hover tooltip "双击编辑"，`dblclick → editSensorPointDialog(id)`
- `✕` 按钮：`deleteSensorPoint(id)`

**温度单位 dropdown 与后处理 tab 的 `_ppTempUnit` 镜像同一变量**，两处任一改，`_ppTempUnit` 统一更新，浮窗 chart 和 list 显示均跟随（数据以 K 存，展示时换算）。

**输入坐标单位** = 全局 `state.inputUnit`（mm/cm/m/inch）。`buildCfdrunJson` 时乘 `unitScale` 换成米，和其它坐标字段一致。

---

## 2 · State Model + Undo + 编辑对话框

### state shape

新增 `state.sensorPoints[]`（顶层字段，参与 undo snapshot/JSON 序列化）：

```js
state.sensorPoints = [
  {
    id: "sensor_1",      // 自动递增；作为 list 的唯一键
    name: "sensor_1",    // 默认 == id，双击编辑对话框里可改；必须 /^[A-Za-z0-9_]+$/ 且全局唯一
    x: 0.5,              // 永远存米（输入时按 state.inputUnit 换算后存米）
    y: 0.2,
    z: 1.8,
    enabled: true        // checkbox：是否进浮窗 chart
  },
  ...
]
```

`id` 生成：`sensor_${maxExistingIndex + 1}`，起点 1。删除时不复用已用过的 index。

### 必须遵守的 mandatory coding rules（CLAUDE.md）

| 动作 | checkpoint label | log category |
|---|---|---|
| `window.addSensorPoint()` | `addSensorPoint` | `create` |
| `window.deleteSensorPoint(id)` | `deleteSensorPoint` | `delete` |
| `window.editSensorPoint(id, patch)` | `editSensorPoint` | `modify` |
| `window.setSensorEnabled(id, bool)` | `toggleSensorEnabled` | `modify` |
| 切 `sensor-temp-unit` | 不单独 checkpoint（走 `_ppTempUnit` 的既有 setter） | `view` |

所有函数都遵守 `pushUndoCheckpoint(...)` 在前、`logOperation(...)` 在后的标准模板。

### 校验（Apply 时执行）

- **名字：** 非空、`/^[A-Za-z0-9_]+$/`、全局唯一
- **坐标：** `state.bounds` 存在时，检查 `(x,y,z)` 必须落在 room bbox 内；超出时 Apply 按钮置灰 + 红字提示 "点超出房间范围"

### 编辑对话框 (`editSensorPointDialog`)

复制 `window.ppEditSlice` 的 floating (非 blocking, 无 backdrop) 模式：

```
┌──── Edit sensor_1 ─────────┐
│ 名字     [ sensor_1      ] │
│ X (mm)   [ 500.0         ] │  ← 单位标签跟 state.inputUnit
│ Y (mm)   [ 200.0         ] │
│ Z (mm)   [ 1800.0        ] │
│                            │
│      [ Cancel ]  [ Apply ] │
└────────────────────────────┘
```

只改 name + xyz。温度单位不在对话框里（全局控制）。`Apply` 触发一次 `editSensorPoint` checkpoint。

---

## 3 · 浮窗折线图 — 第 3 个 tab "传感器"

### 现有结构

`window._smMonitorWindow` 目前有 2 tab：
- `sm-tab-residual` → 数据 `_solverMonitor.residuals`
- `sm-tab-temperature` → 数据 `_solverMonitor.temperatures`

`_smDrawChart` 按 `_smActiveTab` 二分选 datasets。`_smParseLogLine` 逐行扫 solver log（loguru 前缀格式）抓 `Time = N` / 残差 / `areaAverage(...)` 行。

### 新增：第 3 tab

1. **DOM：** `_smBuildMonitorWindow` 的 tab bar 追加 `sm-tab-sensor` 按钮。
2. **数据容器：** `_solverMonitor.sensors = {name: [{step, value}, ...]}`，结构与 `temperatures` 完全一致。
3. **`_smDrawChart` 改动：** `isResidual` 的二分改成 3-way：

   ```js
   var datasets;
   if (_smActiveTab === 'residual')    datasets = mon.residuals;
   else if (_smActiveTab === 'temperature') datasets = mon.temperatures;
   else /* 'sensor' */                 datasets = mon.sensors;
   ```

   Y 轴单位：residual 用 log-scale 标签不动；temperature 和 sensor 都按 `_ppTempUnit` 换算 + 单位标签。

4. **tab 切换：** `_smActiveTab = this.id === 'sm-tab-residual' ? 'residual' : (this.id === 'sm-tab-sensor' ? 'sensor' : 'temperature')`。

### 数据管线 — `window._smSensorPoller`

```
求解启动 (state.sensorPoints.length > 0 且 solve 启动):
  _smSensorPoller.start({ sensorNames: state.sensorPoints.map(p => p.name) })
    lastStep = 0
    fallbackTimer = setInterval(fetchNow, 2000)  // 兜底

_smParseLogLine 抓到 "writing probe data" banner 行:
  _smSensorPoller.fetchNow()

fetchNow():
  GET /api/probes-latest?since=<lastStep>
  → { steps, writeInterval, fields }
  for (i in steps):
    lastStep = max(lastStep, steps[i])
    for (j in sensorNames):
      name = sensorNames[j]
      tempK = fields.T[i][j]
      // 只有 enabled 的才进 chart
      if (state.sensorPoints.find(p => p.name === name).enabled) {
        _solverMonitor.sensors[name] = _solverMonitor.sensors[name] || []
        _solverMonitor.sensors[name].push({ step: steps[i], value: tempK })
      }
  if (_smActiveTab === 'sensor') _smDrawChart()

求解结束 (_solverMonitor.active becomes false):
  _smSensorPoller.stop()  // 清 fallbackTimer
  → 触发 harvest (下节)
```

**关键：** sensor 名字 → probe 索引的映射 = `state.sensorPoints` 数组顺序 = `buildCfdrunJson.sensorPoints[]` 顺序 = controlDict.j2 的 `probeLocations` 顺序 = probes 文件列顺序。poller 启动时拍一个 `sensorNames` 快照，整个求解期间不变。

**温度单位切换：** `_ppTempUnit` 改变时 `_smDrawChart()` 重绘即可，数据不动（永远存 K）。

---

## 4 · Backend — 两个新 API

### API 1：`GET /api/probes-latest?since=<int>`

**路径解析：**
```python
run_dir = _find_latest_run_dir(CASE_DIR)  # 照搬 handle_pp_init L1396-1404
# sorted(CASE_DIR/'log'/*, reverse=True) 找第一个含 constant/polyMesh 的
probes_dir = run_dir / "postProcessing" / "probes"
t0_dir = sorted(probes_dir.iterdir())[0]  # OpenFOAM probes 输出目录名 == probes 启动 time
```

**响应 schema：**

```json
{
  "steps": [10, 20, 30],
  "writeInterval": 10,
  "fields": {
    "T":     [[299.5, 300.1], [299.8, 300.4], [300.1, 300.7]],
    "U":     [[[u,v,w], [u,v,w]], ...],
    "p":     [[p0, p1], ...],
    "p_rgh": [[...], ...],
    "rho":   [[...], ...]
  }
}
```

- `steps[i]` 对应 `fields.T[i]`，probes 数组按探点索引 0..N-1 排。
- 只返回 `step > since` 的行。
- **文件偏移 cache**（per run_dir，模块级 dict）：每次只 `seek` 到上次读到的字节位置续读；run_dir 切换或文件不存在时清缓存。

**错误：**
- CASE_DIR 未设 → 400 `{"error": "No case directory set."}`
- 无 run_dir → 404 `{"error": "No run directory found."}`
- probes_dir 不存在 → 200 `{"steps": [], ...}`（求解刚启动还没 writeInterval 触发）

**probes 文件格式解析** (`parse_probes_file(path, since)`):

```
# Probe 0 (0.5 0.2 1.8)
# Probe 1 (1.0 0.8 2.0)
# Time           probe0          probe1
    10        299.5         300.1
    20        299.8         300.4
```

- 跳过 `^#` 注释行
- 每行空白 split：col[0] = Time（float），col[1..N] = 每个探点的值
- 标量字段（T, p, p_rgh, rho）：值是单浮点
- 向量字段（U）：值是 `(ux uy uz)` 带括号 → 特殊 parse：`re.findall(r"\(([^)]+)\)", line)`

### API 2：`POST /api/harvest-sensor-results`

**请求 body：**
```json
{ "caseName": "CMI-China", "sensorNames": ["sensor_1", "sensor_2"], "unitTemperature": "C" }
```

**server 处理：**

```python
run_dir = _find_latest_run_dir(CASE_DIR)
last_step, fields_all = _parse_probes_last_row(run_dir, ["T","U","p","p_rgh","rho"])
# last_step: int, fields_all: {"T": [...], "U": [...], ...}

today = strftime("%Y-%m-%d")
hms = strftime("%H-%M-%S")
out_path = CASE_DIR / "sensor_results" / today / f"{caseName}_sensor_{hms}.json"
out_path.parent.mkdir(parents=True, exist_ok=True)

# 构造 payload
payload = {
    "version": "1.0",
    "caseName": caseName,
    "solveRunDir": str(run_dir.relative_to(CASE_DIR)),
    "finalStep": last_step,
    "writeInterval": _read_write_interval(run_dir),
    "unitLength": "m",
    "unitTemperature": unitTemperature,
    "generatedAt": datetime.now().isoformat(timespec="seconds"),
    "sensors": [
        {
            "name": name,
            "location": [xi, yi, zi],  # 从 run_dir/system/controlDict 的 probeLocations 里反查
            "T_final": _convertTemp(fields_all["T"][i], unitTemperature),
            "U_final": fields_all["U"][i],     # 保持 SI
            "p_final": fields_all["p"][i],
            "p_rgh_final": fields_all["p_rgh"][i],
            "rho_final": fields_all["rho"][i],
        }
        for i, name in enumerate(sensorNames)
    ],
}

out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
return { "path": str(out_path) }
```

**错误：**
- `len(sensorNames) != 探点列数` → 500 `{"error": "sensor count mismatch: frontend=N, probes=M"}`（防止前后端错位）
- 无 probes 数据 → 500 `{"error": "No probe data found in run directory"}`

### 前端求解结束钩子

既有 `_solverMonitor.active` 从 true 切 false 的位置（约在 `_smParseLogLine` 的退出分支 / 求解完成/中断事件处）新增：

```js
if (state.sensorPoints.length > 0 && wasActive) {
    fetch('/api/harvest-sensor-results', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            caseName: state.dctwinConfig.caseName,
            sensorNames: state.sensorPoints.map(p => p.name),
            unitTemperature: window._ppTempUnit || 'C',
        })
    }).then(r => r.json()).then(r => {
        window._lastSensorJsonPath = r.path;
        logOperation('solve', 'Sensor final values saved: ' + r.path, {}, 'file');
    }).catch(e => {
        logOperation('solve', 'Sensor JSON save failed: ' + e.message, {}, 'error');
    });
}
```

### JSON 落盘路径（final spec）

```
<case>/sensor_results/<YYYY-MM-DD>/<caseName>_sensor_<HH-MM-SS>.json
```

**不**用 `<case>/logger/` —— 避免与既有 `<case>/log/`（OpenFOAM run 目录）只差一字母的视觉混淆。

### "📂 打开最近 sensor JSON" 按钮

```js
window.openLatestSensorJson = function() {
    if (!window._lastSensorJsonPath) {
        alert("尚无已保存的 sensor JSON");
        return;
    }
    // 复用既有"打开文件"API（如 /api/read-file 或 /api/open-file），或直接下载
    window.open('/api/read-file?path=' + encodeURIComponent(window._lastSensorJsonPath));
};
```

---

## 5 · Testing

### 前端结构性测试

新增 `STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py`（照抄现有 `test_refinement_redesign_frontend.py` 的 regex-scan 模式）：

- DOM：`📍 传感器位点` prop-group 存在于 `#solver-settings-panel` 内，位于 `📉 收敛残差阈值设置` 之后、`执行求解` 按钮之前
- DOM：`sensor-temp-unit` / `sensor-input-x/y/z` / "+添加" 按钮 / `sensor-list` / `sensor-open-json-btn` 全部存在
- JS：`window.addSensorPoint` / `deleteSensorPoint` / `editSensorPoint` / `setSensorEnabled` / `editSensorPointDialog` / `openLatestSensorJson` 全部声明
- 所有 state-mutating 函数都出现 `pushUndoCheckpoint(` 和 `logOperation(` 两个调用
- `buildCfdrunJson` 正则匹配 `sensorPoints[...]` 输出，且坐标用 `state.inputUnit` 做了换算
- 名字正则 `[A-Za-z0-9_]+` 校验存在
- bounds 校验（`state.bounds`）存在
- 浮窗：`sm-tab-sensor` 按钮存在、`_solverMonitor.sensors` 容器初始化、`_smActiveTab === 'sensor'` 分支存在

### 后端单元测试

新增 `STLGeneratorForOpenFoam/server/tests/test_probes_api.py`：

- `parse_probes_file()`：
  - 跳过 `# Probe N (x y z)` 头部
  - 标量列解析正确（T/p/p_rgh/rho）
  - 向量列 `(ux uy uz)` 解析正确（U）
  - 空文件 / 只有头部 → 返回空 steps
  - `since` 过滤只返回 `step > since`
- `/api/probes-latest` endpoint：
  - CASE_DIR None → 400
  - 无 run_dir → 404
  - probes_dir 不存在（求解刚启动）→ 200 空结果
  - 正常响应包含 `steps`、`writeInterval`、`fields.{T,U,p,p_rgh,rho}`
- `/api/harvest-sensor-results` endpoint：
  - 写入路径格式 `<case>/sensor_results/<YYYY-MM-DD>/<case>_sensor_<HH-MM-SS>.json`
  - JSON schema 含 version/caseName/solveRunDir/finalStep/writeInterval/unitLength/unitTemperature/generatedAt/sensors[]
  - `sensorNames` 长度与 probes 列数不匹配 → 500
  - `unitTemperature=C` 时 T_final 已按 K→°C 换算

### runner 回归（既有测试跑一遍，不新增）

- `src/backend/aircooling/runner/tests/test_refinement_redesign.py`
- `src/backend/aircooling/runner/tests/test_rack_door_refinement.py`
- `STLGeneratorForOpenFoam/scripts/test_refinement_redesign_frontend.py`

### 人工 E2E（OpenFOAM 依赖）

- 在 CMI-China 或 base604 case 上：
  1. UI 添加 2 个传感器位点（不同坐标）
  2. 执行求解
  3. 浮窗切到"传感器" tab，确认曲线实时刷新（随 probes writeInterval）
  4. 切温度单位 K↔°C↔°F，确认曲线和 list 的 °C 标签同步
  5. 求解完成后确认 `<case>/sensor_results/<YYYY-MM-DD>/<caseName>_sensor_<HH-MM-SS>.json` 存在，schema 正确
  6. 点 "📂 打开最近 sensor JSON" 按钮能跳到文件

---

## Out of Scope / YAGNI

- **多 run 累积的 sensor 历史对比**：本次只写当次求解的 JSON，不做跨求解趋势图。
- **传感器点的 3D 可视化**（在 viewport 显示小球标记）：留给后续；本次只有右边栏文字 list。
- **probes writeInterval 可配置 UI**：沿用 `controlDict.j2` 默认 `10`；如需 tune 由用户改模板。
- **导出 CSV**（Q3=B 明确只 JSON）。
- **每点独立温度单位**（Q1=D 明确全局）。

---

## Related Files（implementer reference）

| 文件 | 角色 |
|---|---|
| `STLGeneratorForOpenFoam/web/STL_generate.html` | UI panel / state / undo / edit dialog / 浮窗 tab / poller / harvest hook |
| `STLGeneratorForOpenFoam/server/server.py` | 两个新 endpoint + probes 文件解析器 |
| `STLGeneratorForOpenFoam/scripts/test_sensor_points_frontend.py` | 新测试 |
| `STLGeneratorForOpenFoam/server/tests/test_probes_api.py` | 新测试 |
| `src/backend/aircooling/runner/stlgen_runner.py:904` | `_extract_sensor_points`（已就位，不改） |
| `src/backend/aircooling/runner/cfdproj_schema.py:46,483` | `SensorPoint` + `sensorPoints` 字段（已就位，不改） |
| `src/backend/aircooling/third_parties/foam/template/system/steady/controlDict.j2:52` | probes 块模板（已就位，不改） |
