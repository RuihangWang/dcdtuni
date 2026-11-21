# 数据中心瞬态 CFD 模拟设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现数据中心 Server 功耗变化 + ACU 状态变化的瞬态 CFD 模拟，支持事件驱动 + 精细时变编辑的混合 UI 范式。

**Architecture:** 前端新增"瞬态场景"Tab + 属性面板 ⏱ 编辑器，数据存储在每个 model 的 `timeVarying` 字段；后端 fields.py 在生成边界条件时检查 timeVarying，有则生成 `uniformFixedValue + table`。

**Tech Stack:** Three.js 前端 (STL_generate.html), Python/Jinja2 后端 (dcdtuni runner), OpenFOAM buoyantBoussinesqPimpleFoam

---

## 1. 核心场景与需求来源

### 1.1 为什么需要瞬态模拟

数据中心瞬态模拟的核心价值是**冷却故障骑越分析 (Cooling Failure Ride-Through)**——某台 CRAC/CRAH 故障停机后，服务器入口温度需要多久超过 ASHRAE A1 上限 (35°C)。稳态无法捕捉温度过冲 (overshoot)——瞬态峰值温度往往高于最终稳态值。

### 1.2 第一版支持的时变参数

| 设备类型 | 参数 | 事件示例 |
|---------|------|---------|
| **ACU (空调)** | 开/关机 | t=60s 时 ACU-1 停机 (风量→0, 供冷→0) |
| **ACU** | 风量变化 | t=120s 时风量从 1.4m³/s 降到 0.7m³/s |
| **ACU** | 供冷温度变化 | 冷冻水故障，供冷温度 15°C → 25°C 爬坡 |
| **Server/机柜** | 总功耗变化 | t=120s 时 R3 功耗 5kW → 10kW |
| **Server/机柜** | 单个 fan-server 功耗变化 | 某个服务器上线/下线 |
| **Server/机柜** | 开/关机 | 功耗→0 |

### 1.3 典型时间尺度

- 风扇惯性停转：10-30 秒
- 冷通道温升速率：最高 5°C/分钟
- 服务器热时间常数：30-120 秒
- 典型模拟时长：5-30 分钟（故障骑越），1-2 小时（冷冻机重启）
- 典型 CFD 时间步：0.5-5 秒（自适应 Courant < 1）

---

## 2. UI 设计（方案 C：事件列表 + 属性面板混合）

### 2.1 总体方案

采用**事件驱动 + 精细编辑混合**方案（brainstorming 中的方案 C）：

- **瞬态场景 Tab**（左侧面板，与建模/网格/求解同级）：全局事件列表 + 时间线甘特图预览 + 执行按钮
- **属性面板 ⏱ 按钮**（右侧属性检查器）：每个 ACU/Rack 参数旁，内联 time-value table 精细编辑
- **双向同步**：事件列表是只读聚合视图（遍历所有 model 的 timeVarying 数据），属性面板是数据编辑源

Mockup 文件保存在 `docs/superpowers/mockups/`:
- `transient-ui-approaches.html` — 三种 UI 方案对比（A/B/C）
- `transient-architecture.html` — 整体架构数据流图
- `transient-frontend-detail.html` — 前端 UI 详细 mockup

### 2.2 瞬态场景 Tab

位于左侧面板，Tab 标签 "⏱ 瞬态"，包含：

1. **事件列表区域**
   - 每行显示：触发时间 | 设备名 | 事件描述
   - 事件行左侧彩色边框区分类型（红=停机, 橙=变化, 绿=正常）
   - 首行固定为 "t=0s 从定常结果初始化"（不可编辑）
   - 点击事件行 → 3D 视口高亮对应设备
   - 右侧 "⋯" 按钮 → 编辑/删除
   - 底部 "+ 添加事件" 按钮

2. **时间线甘特图预览**
   - 每个有 timeVarying 数据的设备一行
   - 水平条形图，颜色编码：绿=正常, 红半透明=停机, 橙=变化中
   - 底部时间轴标注
   - 图例：正常 / 停机 / 变化

3. **执行区域**
   - "▶ 执行瞬态求解" 按钮
   - 提示文字："将自动检测定常结果作为初始场"

### 2.3 添加事件弹窗

点击 "+ 添加事件" 弹出，字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| 目标设备 | 下拉选择 | 列出所有 ACU 和 Rack model |
| 事件类型 | 下拉选择 | ACU: 关机/风量变化/供冷温度变化; Rack: 功耗变化/开关机 |
| 触发时间 (s) | 数字输入 | 事件开始的物理时间 |
| 目标值 | 数字输入 | 变化后的目标值（关机时自动设为 0，灰显） |
| 过渡方式 | 单选 | 阶跃（瞬时）/ 爬坡（线性） |
| 爬坡持续时间 (s) | 数字输入 | 仅在"爬坡"时显示 |

弹窗确认后自动生成 time-value table 写入 `model.timeVarying`：
- 阶跃：`[{t: triggerTime, value: oldValue}, {t: triggerTime+0.1, value: newValue}]`
- 爬坡：`[{t: triggerTime, value: oldValue}, {t: triggerTime+duration, value: newValue}]`

### 2.4 属性面板 ⏱ 内联编辑器

每个支持时变的参数（ACU 风量/供冷温度，Rack 功耗）旁显示 ⏱ 按钮：
- 无时变数据时：灰色 `⏱`
- 有时变数据时：高亮 `⏱✓`

点击 ⏱ 展开内联编辑器：
- time-value 表格（时间列 + 值列 + 删除按钮列）
- "+ 添加行" 按钮
- "清除" 按钮（删除所有时变数据，恢复固定值）
- 每行可直接编辑时间和值

### 2.5 Tab 可见性

- 瞬态场景 Tab **仅在 solverType === 'transient' 时显示**
- 定常模式下隐藏该 Tab，model.timeVarying 数据保留但不生效

---

## 3. 数据结构

### 3.1 model.timeVarying（前端 state）

数据分散存储在每个 model 对象上：

```javascript
// ACU model
model.timeVarying = {
    airflow: [
        { t: 0, value: 1.4 },     // m³/s
        { t: 60, value: 1.4 },
        { t: 60.1, value: 0 }      // 关机
    ],
    supplyTemp: [
        { t: 0, value: 15 },       // °C
        { t: 60, value: 15 },
        { t: 60.1, value: 15 }     // 关机后温度无意义但保持
    ]
}

// Rack model
model.timeVarying = {
    heatOutput: [
        { t: 0, value: 5000 },     // W (总功耗)
        { t: 120, value: 5000 },
        { t: 150, value: 10000 }   // 30s 爬坡到 10kW
    ]
}

// Fan-server (rack 内的 equipment)
// 可选：equipment 级别的时变数据
model.equipment[i].timeVarying = {
    heatOutput: [
        { t: 0, value: 500 },
        { t: 200, value: 0 }       // 关机
    ]
}
```

无时变数据时 `model.timeVarying` 不存在或为空对象 `{}`。

### 3.2 .cfdrun JSON 导出

`buildCfdrunJson()` 导出时，在每个 ACU/Rack 对象中附带 timeVarying：

```json
{
    "acus": {
        "ACU_1": {
            "airflow": 1.4,
            "supplyTemp": 15,
            "timeVarying": {
                "airflow": [{"t": 0, "value": 1.4}, {"t": 60, "value": 1.4}, {"t": 60.1, "value": 0}],
                "supplyTemp": [{"t": 0, "value": 15}, {"t": 60, "value": 15}, {"t": 60.1, "value": 15}]
            }
        }
    },
    "racks": {
        "R3": {
            "totalHeatOutput": 5000,
            "timeVarying": {
                "heatOutput": [{"t": 0, "value": 5000}, {"t": 120, "value": 5000}, {"t": 150, "value": 10000}]
            }
        }
    }
}
```

### 3.3 Pydantic schema (cfdproj_schema.py)

```python
class TimeVaryingEntry(CFDBaseModel):
    t: float
    value: float

class ACUTimeVarying(CFDBaseModel):
    airflow: List[TimeVaryingEntry] = []
    supplyTemp: List[TimeVaryingEntry] = []

class RackTimeVarying(CFDBaseModel):
    heatOutput: List[TimeVaryingEntry] = []
```

---

## 4. 后端实现

### 4.1 fields.py 边界条件生成

在现有 BC 生成逻辑中原地修改。每个 patch 生成 BC 时检查对应 model 的 timeVarying：

```python
# 伪代码
if model_has_time_varying(model, "airflow"):
    table = model.timeVarying.airflow  # [{t, value}, ...]
    bc_type = "uniformFixedValue"
    bc_value = format_of_table(table)   # "table ((0 1.4) (60 1.4) (60.1 0))"
else:
    bc_type = "fixedValue"
    bc_value = f"uniform {airflow_value}"
```

生成的 OpenFOAM BC 格式：

```
ACU_1_inlet
{
    type            uniformFixedValue;
    uniformValue    table
    (
        (0     1.4)
        (60    1.4)
        (60.1  0)
    );
}
```

### 4.2 温度单位转换

前端使用 °C，OpenFOAM 使用 K。timeVarying 的 supplyTemp 在写入 BC 时需要 +273.15：

```
(0    288.15)    // 15°C
(60   288.15)
(60.1 288.15)
```

### 4.3 功耗 → 热源

Rack 的 heatOutput (W) 需要转换为 OpenFOAM 的体积热源 (W/m³)。当前定常模式已有此转换逻辑，瞬态模式复用，只是值变成 time-varying table。

### 4.4 定常→瞬态衔接

执行"瞬态求解"时：
1. 检测 `case/{name}/Mesh/` 下是否有定常收敛结果（latestTime 目录有 U/T/p_rgh 文件）
2. **有结果**：`startFrom latestTime;` 自动从定常结果继续
3. **无结果**：弹窗 "未检测到定常结果，是否先运行定常求解？"
   - 用户选"是"→ 先跑定常到 endTime，完成后自动启动瞬态
   - 用户选"否"→ 从零场启动瞬态（不推荐，初始噪声大）

---

## 5. 已实现 vs 待实现

### 5.1 已实现（当前代码库）

- controlDict: PIMPLE / adjustTimeStep / maxCo / maxDeltaT ✓
- fvSchemes: ddtSchemes (Euler/backward/CrankNicolson) ✓
- fvSolution: PIMPLE dict + 条件 relaxation ✓
- 前端 solver Tab: 瞬态参数面板 (dt模式/写入间隔/ddtScheme/CN系数/PIMPLE外迭代) ✓
- 残差/传感器/服务器温度实时监控 (float time parsing) ✓
- FO writeInterval 解耦（每步写入实时监控数据）✓
- 松弛因子与 PISO/PIMPLE 模式联动 ✓

### 5.2 待实现

| 模块 | 文件 | 工作内容 |
|------|------|---------|
| 数据结构 | cfdproj_schema.py | TimeVaryingEntry, ACUTimeVarying, RackTimeVarying |
| 前端 Tab | STL_generate.html | 新增"⏱ 瞬态"Tab，事件列表 + 时间线甘特图 |
| 前端弹窗 | STL_generate.html | "添加事件"弹窗 UI + 逻辑 |
| 前端属性 | STL_generate.html | ⏱ 按钮 + 内联 time-value table 编辑器 |
| 前端导出 | STL_generate.html | buildCfdrunJson() 附带 timeVarying |
| 后端 BC | fields.py | timeVarying → uniformFixedValue + table |
| 后端衔接 | stlgen_runner.py | 定常→瞬态自动检测 + 弹窗逻辑 |
| i18n | zh-CN.json, en-US.json | 所有新增 UI 元素的国际化 |
| 测试 | test_template_params.py | uniformFixedValue + table BC 生成测试 |
| 测试 | test_schema_completeness.py | timeVarying schema 测试 |

---

## 6. OpenFOAM 技术细节

### 6.1 uniformFixedValue + table BC

OpenFOAM 的 `uniformFixedValue` 配合 `table` Function1，接受时间-值对列表，自动线性插值：

```
inlet
{
    type            uniformFixedValue;
    uniformValue    table
    (
        (0      293.15)     // t=0s, 20°C
        (60     293.15)     // t=60s, 保持
        (60.1   323.15)     // t=60.1s, 阶跃到 50°C
    );
}
```

- 两个时间点之间自动线性插值
- 超出范围的时间使用最近的端点值（clamp）
- 标量（温度、压力）和向量（速度）都支持

### 6.2 向量场 (velocity) 的 table

ACU 风量变化需要写速度向量的 table：

```
ACU_1_inlet
{
    type            uniformFixedValue;
    uniformValue    table
    (
        (0      (0 -2.5 0))     // 正常风速
        (60     (0 -2.5 0))
        (60.1   (0 0 0))        // 停机
    );
}
```

velocity 值需要从 airflow (m³/s) 除以 patch 面积得到。当前定常模式已有此计算。

### 6.3 热源时变

Rack 热源通过 fvOptions (fixedTemperatureConstraint 或 scalarCodedSource) 设置。时变热源需要使用 `coded` 类型或 `Function1` 参数：

```
// fvOptions 中的时变热源（方案待定）
heatSource_R3
{
    type            scalarCodedSource;
    // 或使用 fixedPower + table
    selectionMode   cellZone;
    cellZone        R3_zone;
    volumeMode      absolute;
    injectionRateSuT  table ((0 5000) (120 5000) (150 10000));
}
```

当前定常模式使用 `fixedPower` 类型设定机柜热源（`fields.py` 中 `_write_T_field` 相关逻辑），瞬态模式可将 `injectionRateSuT` 改为 `table` Function1。若 OpenFOAM v2406 的 `fixedPower` 不支持 Function1 参数，则回退到在每个时间步前通过 Python 脚本修改 fvOptions 文件的方式（类似 `codedFixedValue` 但在求解器外部）。implementation plan 阶段验证具体可行性。

---

## 7. 约束与边界

- 第一版不支持几何变化（开门、移除盲板等）
- 第一版不支持多物理场耦合（如服务器固体热容建模）
- 时变数据仅支持分段线性插值（OpenFOAM table 默认行为）
- 定常结果必须存在才能启动瞬态（或用户显式选择零场启动）
- 前端 timeVarying 数据必须 JSON-serializable，遵循现有 state 序列化规则
