# STL Generator i18n (Multi-Language) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete internationalization (i18n) system to `STLGeneratorForOpenFoam/STL_generate.html` so the UI can switch between Chinese (zh-CN, default) and English (en-US), with the architecture supporting additional languages.

**Architecture:** Lightweight custom i18n module embedded inline in the HTML file. Language dictionaries stored as external JSON files loaded via `fetch()`. HTML elements use `data-i18n` attributes for static text; JS code uses a global `t(key, vars)` function for dynamic text. Language preference persisted in `localStorage`.

**Tech Stack:** Vanilla JavaScript (no external i18n library), JSON language files, existing server.py for file serving.

### Important Design Decisions

1. **Emojis are part of translated strings.** Every emoji (💾, 🗂️, etc.) must appear in BOTH zh-CN.json and en-US.json values. The `applyToDOM()` method replaces `textContent` entirely, so if an emoji is missing from a translation, it disappears.

2. **`<select>/<option>` elements** are dynamically generated in JS inspector functions via `innerHTML`. They CANNOT use `data-i18n` attributes (they are destroyed/rebuilt each `updateUI()` cycle). Use inline `t()` calls during innerHTML construction instead.

3. **Async loading fallback:** `I18n.init()` is async (uses `fetch()`). Before dictionaries load, `t(key)` returns the raw key. The original Chinese text kept as HTML innerHTML serves as the visual fallback until `applyToDOM()` runs after loading completes. This is intentional — the UI is functional in Chinese even if JSON loading fails.

4. **`logOperation()` messages (157 calls):** These appear in the user-visible Terminal panel and WILL be translated. They are NOT developer-only console messages. Add `log.*` keys for these strings.

5. **`console.warn()`/`console.error()` messages:** These go to the browser developer console only and are NOT translated. Leave as-is.

6. **Pluralization:** Not supported in v1. Use patterns like "{count} model(s)" in English or always use the plural form. Note as a limitation for future languages.

7. **RTL languages:** Out of scope. The CSS uses LTR layout assumptions throughout.

8. **JSON merge conflicts:** Tasks 3-9 all modify the same two JSON files. If executed in parallel, merge conflicts on JSON files are expected but easily resolvable (key additions are non-conflicting). Agents should merge JSON keys carefully.

---

## File Structure

```
STLGeneratorForOpenFoam/
├── STL_generate.html          # MODIFY: Add i18n module, data-i18n attrs, t() calls
├── i18n/
│   ├── zh-CN.json             # CREATE: Chinese dictionary (~800 keys)
│   └── en-US.json             # CREATE: English dictionary (~800 keys)
└── server.py                  # MODIFY: 6 Chinese log strings → i18n (low priority)
```

### Key Naming Convention

Keys are organized hierarchically by UI section:

| Prefix | Scope | Example |
|--------|-------|---------|
| `nav.*` | Tab labels, navigation | `nav.geometry`, `nav.mesh` |
| `btn.*` | Button text | `btn.save_project`, `btn.export_stl` |
| `label.*` | Form labels, field names | `label.base_size`, `label.max_cells` |
| `title.*` | Section/panel titles | `title.mesh_global`, `title.refinement` |
| `msg.*` | Info/success messages | `msg.screenshot_saved` |
| `error.*` | Error messages | `error.no_canvas`, `error.exceeds_boundary` |
| `tooltip.*` | HTML title attributes | `tooltip.configure_dctwin` |
| `hint.*` | Helper/description text | `hint.max_cells_desc` |
| `tree.*` | Tree view labels | `tree.root_node`, `tree.empty_project` |
| `mesh.*` | Mesh panel specific | `mesh.parallel_mesh`, `mesh.procs` |
| `solver.*` | Solver panel specific | `solver.type`, `solver.iterations` |
| `post.*` | Post-processing panel | `post.surface_temp`, `post.streamlines` |
| `inspector.*` | Property inspector | `inspector.title`, `inspector.project_settings` |
| `category.*` | Model category labels | `category.box`, `category.rack` |
| `physics.*` | CFD physics labels | `physics.supply_temp`, `physics.flow_rate` |
| `bool.*` | Bool Diff operation | `bool.target`, `bool.tool`, `bool.execute` |
| `toolbar.*` | Toolbar buttons/labels | `toolbar.rotate_x`, `toolbar.face_snap` |
| `common.*` | Shared terms | `common.apply`, `common.cancel`, `common.display` |

### Variable Interpolation

Template strings use `{varName}` placeholders:
```json
{
  "msg.screenshot_saved": "截图已保存: {path}",
  "tree.multi_model": "多模型工程 {count}",
  "msg.generated_regions": "已生成 {count} 个投影区域"
}
```

Called as: `t('msg.screenshot_saved', { path: result.path })`

---

## Task 1: Create i18n Infrastructure Module

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (after line 537 `<script>`, before existing code)
- Create: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Create: `STLGeneratorForOpenFoam/i18n/en-US.json`

This task creates the core i18n engine and a minimal set of strings to prove it works.

- [ ] **Step 1: Create the i18n directory**

```bash
mkdir -p STLGeneratorForOpenFoam/i18n
```

- [ ] **Step 2: Create minimal zh-CN.json with 10 test strings**

Create `STLGeneratorForOpenFoam/i18n/zh-CN.json`:
```json
{
  "_meta": {
    "locale": "zh-CN",
    "name": "简体中文",
    "version": "1.0.0"
  },
  "nav.geometry": "🗂️ 几何与属性",
  "nav.mesh": "⚙️ 网格",
  "nav.solver": "🔬 求解",
  "nav.postprocess": "📊 后处理",
  "btn.save_project": "💾 保存工程",
  "btn.load_project": "📂 加载工程",
  "btn.import_stl": "🛸 导入水密曲面 STL 进行水转印",
  "btn.export_stl": "📥 导出 CFD 流形拓扑 STL",
  "btn.export_cfd_package": "🔬 导出 CFD 工程包 (JSON+STL)",
  "title.page": "V5.0 - PRO 工业级 CFD 拓扑重构与水转印系统"
}
```

- [ ] **Step 3: Create minimal en-US.json with same 10 keys**

Create `STLGeneratorForOpenFoam/i18n/en-US.json`:
```json
{
  "_meta": {
    "locale": "en-US",
    "name": "English",
    "version": "1.0.0"
  },
  "nav.geometry": "🗂️ Geometry & Properties",
  "nav.mesh": "⚙️ Mesh",
  "nav.solver": "🔬 Solver",
  "nav.postprocess": "📊 Post-processing",
  "btn.save_project": "💾 Save Project",
  "btn.load_project": "📂 Load Project",
  "btn.import_stl": "🛸 Import Watertight STL for Projection",
  "btn.export_stl": "📥 Export CFD Manifold STL",
  "btn.export_cfd_package": "🔬 Export CFD Package (JSON+STL)",
  "title.page": "V5.0 - PRO Industrial CFD Topology & Water-Transfer System"
}
```

- [ ] **Step 4: Add the I18n module to STL_generate.html**

Insert immediately after line 537 (`<script>`), before any existing code:

```javascript
/* ═══════════════════════════════════════════════════════
 *  I18n — Lightweight internationalization module
 * ═══════════════════════════════════════════════════════ */
const I18n = {
    locale: localStorage.getItem('stlgen-lang') || 'zh-CN',
    fallbackLocale: 'zh-CN',
    dictionaries: {},
    _ready: false,
    _readyCallbacks: [],

    async init() {
        await this.load(this.locale);
        if (this.locale !== this.fallbackLocale) {
            await this.load(this.fallbackLocale);
        }
        this._ready = true;
        this._readyCallbacks.forEach(cb => cb());
        this._readyCallbacks = [];
    },

    async load(locale) {
        if (this.dictionaries[locale]) return;
        try {
            const resp = await fetch(`i18n/${locale}.json`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            this.dictionaries[locale] = await resp.json();
        } catch (e) {
            console.warn(`[I18n] Failed to load ${locale}:`, e);
            this.dictionaries[locale] = {};
        }
    },

    t(key, vars = {}) {
        const dict = this.dictionaries[this.locale] || {};
        const fallback = this.dictionaries[this.fallbackLocale] || {};
        let text = dict[key] || fallback[key] || key;
        for (const [k, v] of Object.entries(vars)) {
            text = text.replaceAll(`{${k}}`, v);
        }
        return text;
    },

    async setLocale(locale) {
        await this.load(locale);
        this.locale = locale;
        localStorage.setItem('stlgen-lang', locale);
        document.documentElement.lang = locale;
        document.title = this.t('title.page');
        this.applyToDOM();
        // Re-render dynamic UI
        if (typeof updateUI === 'function') updateUI();
    },

    applyToDOM() {
        document.querySelectorAll('[data-i18n]').forEach(el => {
            el.textContent = this.t(el.getAttribute('data-i18n'));
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            el.title = this.t(el.getAttribute('data-i18n-title'));
        });
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            el.placeholder = this.t(el.getAttribute('data-i18n-placeholder'));
        });
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            el.innerHTML = this.t(el.getAttribute('data-i18n-html'));
        });
    },

    onReady(cb) {
        if (this._ready) cb();
        else this._readyCallbacks.push(cb);
    }
};

// Global shorthand
function t(key, vars) { return I18n.t(key, vars); }

// Initialize i18n (non-blocking — dictionaries load in background)
I18n.init();
```

- [ ] **Step 5: Add data-i18n attributes to the 10 test elements**

Modify the HTML elements (lines ~189-207):

```html
<!-- Before -->
<button class="file-btn" onclick="saveProject()">💾 保存工程</button>

<!-- After -->
<button class="file-btn" onclick="saveProject()" data-i18n="btn.save_project">💾 保存工程</button>
```

Apply `data-i18n` to these 10 elements:
- Line 5: `<title>` → set via JS in `I18n.init()` (no data-i18n needed)
- Line 189: save button → `data-i18n="btn.save_project"`
- Line 190: import button → `data-i18n="btn.import_stl"`
- Line 191: load button → `data-i18n="btn.load_project"`
- Line 194: geometry tab → `data-i18n="nav.geometry"`
- Line 195: mesh tab → `data-i18n="nav.mesh"`
- Line 196: solver tab → `data-i18n="nav.solver"`
- Line 197: postprocess tab → `data-i18n="nav.postprocess"`
- Line 206: export STL button → `data-i18n="btn.export_stl"`
- Line 207: export CFD button → `data-i18n="btn.export_cfd_package"`

- [ ] **Step 6: Hook I18n.applyToDOM() into the init flow**

Find where the app initializes (the DOMContentLoaded or inline init block) and add:
```javascript
I18n.onReady(() => {
    I18n.applyToDOM();
});
```

- [ ] **Step 7: Verify the 10 test strings work**

Start the server and open in browser:
```bash
cd /mnt/lv/litianyu/mycode/dcdtuni/STLGeneratorForOpenFoam
python server.py --case-dir ../dctwin/case/V5_CFD_Station --port 8001
```
Open `http://localhost:8001/STL_generate.html`. Verify:
1. Default language is zh-CN, buttons show Chinese text
2. Open console: `I18n.setLocale('en-US')` — buttons switch to English
3. Refresh page — English persists (localStorage)
4. `I18n.setLocale('zh-CN')` — back to Chinese

- [ ] **Step 8: Commit**

```bash
git add STLGeneratorForOpenFoam/i18n/ STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat(i18n): add i18n infrastructure with I18n module and initial zh-CN/en-US dictionaries"
```

---

## Task 2: Add Language Selector UI

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (HTML toolbar area ~line 467, CSS ~line 16)

- [ ] **Step 1: Add CSS for the language selector**

Insert in the `<style>` block (around line 56, after toolbar styles):

```css
#lang-selector {
    background: #333; color: #ccc; border: 1px solid #555;
    padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;
    cursor: pointer; margin-left: auto; flex-shrink: 0;
}
#lang-selector:hover { background: #444; border-color: var(--highlight); }
```

- [ ] **Step 2: Add the language selector dropdown to the toolbar**

Insert before the closing `</div>` of `#toolbar-content` (line ~494, before `</div>` that closes toolbar-content):

```html
<select id="lang-selector" onchange="I18n.setLocale(this.value)">
    <option value="zh-CN">中文</option>
    <option value="en-US">English</option>
</select>
```

- [ ] **Step 3: Sync selector with stored locale on init**

Add to the `I18n.init()` method, after `this._ready = true`:

```javascript
// Sync language selector UI
const sel = document.getElementById('lang-selector');
if (sel) sel.value = this.locale;
```

- [ ] **Step 4: Verify language selector works**

Reload the page. The dropdown should appear at the right end of the toolbar. Selecting "English" switches the 10 test strings; selecting "中文" switches back. Preference persists across reloads.

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html
git commit -m "feat(i18n): add language selector dropdown to toolbar"
```

---

## Task 3: Extract All HTML Static Strings (File Bar + Tabs + Headers)

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (lines 186-536)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

This task covers all Chinese text in the HTML section (lines 186-536) that is NOT dynamically generated by JavaScript. These are static text in HTML elements: buttons, labels, spans, divs, section titles, and descriptions.

- [ ] **Step 1: Add all HTML static string keys to zh-CN.json**

Add to `zh-CN.json` (append to existing keys). The full list of strings to extract from lines 186-536:

**File bar (lines 188-191):**
Already done in Task 1.

**Left panel headers (lines 199-201):**
```json
"tree.header_title": "🗂️ 几何拓扑特征树",
"tree.mode_parametric": "参数化模式",
"tree.mode_multi": "多模型工程"
```

**Mesh settings panel (lines 210-320):**
```json
"title.mesh_global": "🌐 网格全局参数",
"label.base_size": "基础尺寸 (m)",
"label.max_cells": "最大网格数",
"hint.max_cells_desc": "snappyHexMesh 的 maxGlobalCells，到达上限后停止加密。",
"label.parallel_mesh": "并行网格",
"label.enabled": "启用",
"label.procs": "进程数",
"title.refinement": "🔬 加密策略",
"hint.refinement_desc": "表面 [min, max]：控制几何表面附近网格密度。特征线：控制转角/棱边附近网格密度（建议 ≥ 表面 max）。",
"label.surface_min": "表面 min",
"label.surface_max": "表面 max",
"label.feature_line": "特征线",
"label.room_wall": "房间墙壁",
"label.rack": "机架",
"label.server_face": "服务器面",
"label.acu": "ACU",
"label.white_model": "白模",
"label.baffle_floor": "挡板/地板",
"title.blockmesh": "🧊 BlockMesh 计算域",
"hint.blockmesh_desc": "手动设置 blockMesh 的计算域包围盒（m）。",
"btn.set_blockmesh": "🖊️ 设置 BlockMesh 范围",
"hint.blockmesh_drag": "🖱️ 鼠标悬停面边框高亮，拖拽调整范围；右边栏可切换移动模式。",
"btn.resize_mode": "↔️ 调整大小",
"btn.move_mode": "↔️ 整体移动",
"btn.auto_fit": "🔄 自动适配模型",
"btn.preview_grid": "👁 预览背景网格",
"title.refinement_zones": "📦 体积加密区域 (Refinement Zones)",
"hint.refinement_zones_desc": "定义 searchableBox 体积加密区域，在指定区域内增加网格密度。",
"btn.add_zone": "➕ 添加加密区域",
"label.location_in_mesh": "显示",
"hint.location_desc": "流体域内一点（snappyHexMesh 保留此点所在区域的网格）。",
"label.drag_axis": "拖拽轴约束",
"btn.locate_view": "📷 定位视角到该点",
"btn.run_mesh": "🚀 执行网格生成",
"btn.abort_mesh": "⏹ 中断网格生成"
```

**Solver settings panel (lines 322-356):**
```json
"hint.solver_no_select": "求解模式下几何无法被选中，所有选中状态已清除。如需修改几何，请切换到「几何与属性」选项卡。",
"hint.solver_no_select_short": "求解模式下几何无法被选中",
"title.solver_settings": "⚙️ 求解器设置",
"label.solver_type": "求解器类型",
"label.steady": "定常",
"label.transient": "非定常",
"label.iterations": "迭代步数 (endTime)",
"label.write_interval": "写入间隔",
"label.parallel_solve": "并行求解",
"hint.parallel_solve_desc": "勾选后使用网格生成的并行配置（decomposePar 已在 mesh 阶段完成）。",
"title.gravity": "🌍 重力设置",
"hint.gravity_desc": "设置重力加速度方向和大小（m/s²）。默认 Z 轴向下 -9.81。",
"btn.check_mesh": "🔍 检测网格状态",
"btn.run_solve": "🚀 执行求解",
"btn.abort_solve": "⏹ 中断求解"
```

**Post-processing panel (lines 360-520):**
```json
"hint.postprocess_desc": "📊 后处理模式：可视化 CFD 求解结果。加载求解数据后可渲染温度场、速度场和流线。",
"title.result_selection": "📂 结果选择",
"label.solve_records": "求解记录",
"label.iteration_step": "迭代步",
"btn.enable_postprocess": "开启后处理",
"title.render_control": "🎨 渲染控制",
"label.surface_temp": "🌡️ 表面温度场",
"btn.delete_temp": "❌ 删除温度场",
"label.colorbar_range": "Color Bar 范围:",
"btn.show_colorbar": "显示ColorBar",
"btn.hide_colorbar": "隐藏ColorBar",
"label.surface_velocity": "💨 表面速度场",
"btn.delete_velocity": "❌ 删除速度场",
"label.component": "分量:",
"title.streamlines": "🌊 添加流线",
"label.color_by": "着色:",
"label.color_temperature": "温度 (T)",
"label.color_velocity": "速度幅值 (|U|)",
"label.color_pressure": "压力 (p)",
"label.color_tke": "湍动能 (k)",
"label.seed_points": "撒点数量",
"label.sphere_x": "球心 X",
"label.sphere_y": "球心 Y",
"label.sphere_z": "球心 Z",
"label.sphere_radius": "半径",
"btn.apply": "✅ 应用",
"btn.clear_render": "🗑️ 清除全部渲染",
"title.display_options": "👁️ 显示选项",
"label.show_geometry": "显示原始几何",
"title.patch_visibility": "📊 Patch 可见性",
"btn.select_all": "全选",
"btn.deselect_all": "全不选",
"label.read_zones": "读取 Zones (faceZone / cellZone)",
"btn.apply_short": "应用"
```

**Toolbar (lines 467-496):**
```json
"toolbar.expand": "▶ 展开工具栏",
"toolbar.rotate_x": "X轴 90°",
"toolbar.rotate_y": "Y轴 90°",
"toolbar.rotate_z": "Z轴 90°",
"toolbar.mirror_x": "🪞 X向镜像",
"toolbar.mirror_y": "🪞 Y向镜像",
"toolbar.reset_transform": "✖ 重置变换",
"toolbar.face_snap": "🧲 面吸附",
"toolbar.edge_align": "📏 边对齐",
"toolbar.grid_snap": "📐 网格吸附",
"toolbar.show_grid": "显示 XOY 网格",
"toolbar.hide_grid": "隐藏 XOY 网格",
"toolbar.grid_spacing": "网格间距",
"toolbar.only_cube_rotate": "仅拖动方块旋转视角"
```

**Right panel + misc (lines 498-535):**
```json
"inspector.title": "⚙️ 属性检查器 (Inspector)",
"btn.screenshot": "📸 截图"
```

- [ ] **Step 2: Add corresponding en-US translations**

Add to `en-US.json` with same keys, English values. Examples:
```json
"title.mesh_global": "🌐 Global Mesh Parameters",
"label.base_size": "Base Size (m)",
"label.max_cells": "Max Mesh Cells",
"hint.max_cells_desc": "snappyHexMesh maxGlobalCells — refinement stops when this limit is reached.",
"label.parallel_mesh": "Parallel Mesh",
"label.enabled": "Enabled",
"label.procs": "Processes",
"toolbar.face_snap": "🧲 Face Snap",
"toolbar.edge_align": "📏 Edge Align",
"toolbar.grid_snap": "📐 Grid Snap",
"toolbar.show_grid": "Show XOY Grid",
"toolbar.hide_grid": "Hide XOY Grid",
"toolbar.grid_spacing": "Grid Spacing",
"btn.run_mesh": "🚀 Run Mesh",
"btn.abort_mesh": "⏹ Abort Mesh",
"btn.run_solve": "🚀 Run Solver",
"btn.abort_solve": "⏹ Abort Solver"
```
...and so on for all keys defined in Step 1.

- [ ] **Step 3: Add data-i18n attributes to all HTML elements**

For each element with Chinese text in lines 186-536, add `data-i18n="key"` (for textContent), `data-i18n-title="key"` (for title attr), or `data-i18n-html="key"` (for innerHTML with formatting).

**Pattern for simple text:**
```html
<!-- Before -->
<div class="prop-title" style="color:#56b6c2">🌐 网格全局参数</div>

<!-- After -->
<div class="prop-title" style="color:#56b6c2" data-i18n="title.mesh_global">🌐 网格全局参数</div>
```

**Pattern for title attributes:**
```html
<!-- Before -->
<button ... title="请先在属性检查器中配置并验证 DCTwin 路径">⚙️ 网格</button>

<!-- After -->
<button ... data-i18n="nav.mesh" data-i18n-title="tooltip.configure_dctwin">⚙️ 网格</button>
```

**Important**: Keep the original Chinese text as the innerHTML fallback — if JSON fails to load, the UI still works in Chinese.

Apply to ALL ~100 HTML elements with Chinese text between lines 186-536.

- [ ] **Step 4: Verify all HTML static strings switch correctly**

Reload the page. Open console:
```javascript
I18n.setLocale('en-US')  // All static text → English
I18n.setLocale('zh-CN')  // All static text → Chinese
```
Visually inspect each panel: file bar, tabs, mesh settings, solver settings, post-processing, toolbar, right panel.

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract all HTML static strings to language dictionaries"
```

---

## Task 4: Extract JavaScript Alert and Confirm Strings

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (JS section, lines 537+)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

This covers all `alert()` and `confirm()` calls with Chinese text. These are the easiest JS strings to extract because they are standalone calls.

- [ ] **Step 1: Identify all alert/confirm calls with Chinese text**

Use grep to find all instances:
```bash
cd STLGeneratorForOpenFoam
grep -n "alert(" STL_generate.html | grep -P '[\x{4e00}-\x{9fff}]'
grep -n "confirm(" STL_generate.html | grep -P '[\x{4e00}-\x{9fff}]'
```

- [ ] **Step 2: Add alert/confirm keys to zh-CN.json**

Add keys for every alert/confirm message found. Examples:

```json
"error.no_canvas": "找不到渲染画布",
"error.screenshot_failed": "截图失败: {error}",
"error.exceeds_boundary": "操作被撤回：模型超出了工程边界限制。请撤销或手动调整。",
"error.bool_target_type": "被减对象必须是地板或挡板类型。",
"error.no_bounds": "无法获取减除对象的边界。",
"error.no_overlap": "减除对象与被减对象没有重合区域。",
"error.no_floor_overlap": "没有找到与地板重合的模型。",
"error.no_wall_overlap": "没有找到与墙壁平面重合的模型。",
"error.stl_preview_failed": "STL Preview 刷新失败：{error}",
"error.no_models_for_preview": "当前工程没有模型可用于 STL Preview。",
"error.view_switch_failed": "视图切换失败：{error}",
"error.cannot_create_exceeds": "无法创建：默认模型超出了工程边界限制。请调整工程尺寸后重试。"
```

- [ ] **Step 3: Add corresponding en-US translations**

```json
"error.no_canvas": "Cannot find render canvas",
"error.screenshot_failed": "Screenshot failed: {error}",
"error.exceeds_boundary": "Operation undone: model exceeds project boundary. Please undo or adjust manually.",
"error.bool_target_type": "Target must be a Floor or Baffle type.",
"error.no_bounds": "Cannot get tool object bounds.",
"error.no_overlap": "Tool and target have no overlap region.",
"error.no_floor_overlap": "No models overlap with the floor.",
"error.no_wall_overlap": "No models overlap with the wall plane.",
"error.stl_preview_failed": "STL Preview refresh failed: {error}",
"error.no_models_for_preview": "No models available for STL Preview.",
"error.view_switch_failed": "View switch failed: {error}",
"error.cannot_create_exceeds": "Cannot create: default model exceeds project boundary. Adjust project size first."
```

- [ ] **Step 4: Replace alert/confirm calls in JS**

**Pattern for simple alerts:**
```javascript
// Before
alert('找不到渲染画布');

// After
alert(t('error.no_canvas'));
```

**Pattern for template literal alerts:**
```javascript
// Before
alert(`截图失败: ${err.message}`);

// After
alert(t('error.screenshot_failed', { error: err.message }));
```

**Pattern for confirm calls:**
```javascript
// Before
if (!confirm('确定要删除此模型吗？')) return;

// After
if (!confirm(t('confirm.delete_model'))) return;
```

Replace ALL alert/confirm calls found in Step 1.

- [ ] **Step 5: Verify alerts show in correct language**

1. Set locale to en-US via console
2. Trigger various error conditions (e.g., try to create a model that exceeds boundary)
3. Verify the alert text is in English
4. Switch back to zh-CN, verify Chinese

- [ ] **Step 6: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract all alert/confirm messages to language dictionaries"
```

---

## Task 5: Extract JavaScript Dynamic UI Strings — Tree View

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (lines ~6700-6780, the `updateUI` / tree rendering code)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

The tree view is rebuilt dynamically via JS `innerHTML`. All Chinese strings in the tree rendering functions need `t()` calls.

- [ ] **Step 1: Add tree view keys to both JSON files**

**zh-CN.json additions:**
```json
"tree.header_title": "🗂️ 几何拓扑特征树",
"tree.root_node": "🗂️ 工程根节点",
"tree.empty_project": "⚪ 当前工程没有模型，点击左上角导入 STL 或新建默认白模",
"tree.mode_multi": "多模型工程 {count}",
"tree.mode_parametric": "参数工程 {count}",
"category.box": "白模 (BOX)",
"category.rack": "机柜 (RACK)",
"category.acu": "空调 (ACU)",
"category.wall": "墙壁 (WALL)",
"category.baffle": "挡板 (BAFFLE)",
"category.floor": "地板 (FLOOR)",
"category.custom": "自定义 (CUSTOM)"
```

**en-US.json additions:**
```json
"tree.header_title": "🗂️ Geometry Topology Tree",
"tree.root_node": "🗂️ Project Root",
"tree.empty_project": "⚪ No models — import an STL or create a default box from the top-left buttons",
"tree.mode_multi": "Multi-Model {count}",
"tree.mode_parametric": "Parametric {count}",
"category.box": "Box (BOX)",
"category.rack": "Rack (RACK)",
"category.acu": "ACU (ACU)",
"category.wall": "Wall (WALL)",
"category.baffle": "Baffle (BAFFLE)",
"category.floor": "Floor (FLOOR)",
"category.custom": "Custom (CUSTOM)"
```

- [ ] **Step 2: Replace Chinese strings in tree rendering code**

Around lines 6703-6731:

```javascript
// Before (line 6703)
if (treeHeader) treeHeader.textContent = '🗂️ 几何拓扑特征树';

// After
if (treeHeader) treeHeader.textContent = t('tree.header_title');

// Before (line 6706)
document.getElementById('mode-badge').innerText = nonBoxCount > 0
    ? `多模型工程 ${state.models.length}`
    : `参数工程 ${state.models.length}`;

// After
document.getElementById('mode-badge').innerText = nonBoxCount > 0
    ? t('tree.mode_multi', { count: state.models.length })
    : t('tree.mode_parametric', { count: state.models.length });

// Before (line 6714)
...🗂️ 工程根节点...

// After
...${t('tree.root_node')}...

// Before (line 6721)
⚪ 当前工程没有模型，点击左上角导入 STL 或新建默认白模

// After
${t('tree.empty_project')}

// Before (lines 6725-6731) — categoryOrder labels
{ kind: 'box', icon: '📦', label: '白模 (BOX)' },

// After
{ kind: 'box', icon: '📦', label: t('category.box') },
```

Apply the same pattern to all category labels and tree text.

- [ ] **Step 3: Replace Chinese strings in inspector title rendering**

Around lines 6754-6763:

```javascript
// Before
state.selection.type === 'project' ? '🗂️ 工程设定' :

// After
state.selection.type === 'project' ? t('inspector.project_settings') :
```

Add keys for all inspector titles:
```json
"inspector.project_settings": "🗂️ 工程设定",
"inspector.multi_select": "🔗 多选操作 ({count})",
"inspector.wall_settings": "🧱 墙壁设定",
"inspector.baffle_settings": "📄 挡板设定",
"inspector.floor_settings": "🟫 地板设定",
"inspector.acu_settings": "❄️ 空调设定",
"inspector.model_settings": "📦 模型设定",
"inspector.face_projection": "📄 面投影操作",
"inspector.wall_region": "🧱 剩余面区域",
"inspector.region_info": "🧩 Region 信息",
"inspector.uslot_settings": "🪟 U-slot 设置",
"inspector.equipment_settings": "🧰 设备设置",
"inspector.partition_settings": "🪟 隔板设置",
"inspector.patch_editor": "⚙️ Patch 属性编辑"
```

- [ ] **Step 4: Verify tree view and inspector titles**

1. Reload page with a loaded project (with models)
2. Switch to en-US → tree categories, root node, mode badge, inspector titles all in English
3. Switch to zh-CN → all back to Chinese
4. Test with empty project (no models) → empty state message translated

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract tree view and inspector title strings"
```

---

## Task 6a: Extract Inspector Strings — Root, DCTwin Config, Bounds, Room Temp

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (functions: `renderRootInspector`, `renderDctwinConfigSection`, `_renderRoomTempUI`, `renderBoundsAxisRow`, `anchorControlsForModel`)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

Inspector panels are generated entirely in JS via template literals (`innerHTML`). All Chinese strings use inline `t()` calls (NOT `data-i18n` attributes, since the DOM is rebuilt each `updateUI()` cycle).

- [ ] **Step 1: Add root inspector + DCTwin config keys to JSON files**

**zh-CN.json additions:**
```json
"inspector.project_name": "项目名称",
"inspector.project_unit": "单位",
"inspector.room_dimensions": "房间尺寸",
"inspector.room_width": "宽度",
"inspector.room_depth": "深度",
"inspector.room_height": "高度",
"inspector.dctwin_path": "DCTwin 工程路径",
"inspector.validate_path": "验证路径",
"inspector.dctwin_status": "状态",
"inspector.dctwin_connected": "可用",
"inspector.dctwin_disconnected": "未连接",
"inspector.dctwin_unavailable": "不可用",
"inspector.dctwin_set_case": "设置 Case 目录",
"inspector.dctwin_ready": "已就绪",
"inspector.room_temp": "室温 (°C)",
"inspector.move_mode": "移动模式",
"inspector.move_btn": "启用移动",
"inspector.stop_move_btn": "停止移动",
"inspector.show_anchor": "显示锚点圆球",
"inspector.axis_constraint": "轴向约束",
"inspector.anchor_displacement": "模型锚点位移",
"inspector.geometry_move": "几何移动"
```

(Plus en-US translations for all above.)

- [ ] **Step 2: Replace Chinese strings in renderRootInspector()**

Find `function renderRootInspector` and replace every Chinese literal with `t('key')`.

- [ ] **Step 3: Replace Chinese strings in renderDctwinConfigSection()**

Around lines 4988-5021.

- [ ] **Step 4: Replace Chinese strings in _renderRoomTempUI() and anchorControlsForModel()**

Around lines 5023+ and 5069-5090.

- [ ] **Step 5: Verify and commit**

Load a project, click on project root → verify root inspector in both languages.

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract root inspector and DCTwin config strings"
```

---

## Task 6b: Extract Inspector Strings — Model Inspector (Box, Wall, Baffle, Floor, ACU, Custom)

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (functions: `renderModelInspector`, `renderWallBaffleInspector`, `renderAcuModelInspector`, `renderMultiSelectInspector`)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

- [ ] **Step 1: Add model inspector keys to JSON files**

**zh-CN.json additions:**
```json
"inspector.position": "位置",
"inspector.size": "尺寸",
"inspector.rotation": "旋转",
"inspector.model_name": "模型名称",
"inspector.model_type": "模型类型",
"inspector.lock_position": "锁定位置",
"btn.delete_model": "🗑️ 删除模型",
"btn.duplicate_model": "📋 复制模型",
"btn.enable_move": "启用移动",
"btn.stop_move": "停止移动",
"inspector.wall_name": "墙壁命名",
"inspector.floor_name": "地板命名",
"inspector.baffle_name": "挡板命名",
"btn.add_circle_patch": "添加圆形 Patch",
"btn.add_rect_patch": "添加矩形 Patch",
"btn.auto_bool_diff": "自动 Bool Diff",
"btn.auto_cutout": "自动挖除重合区域",
"btn.clear_cutouts": "清除所有挖除",
"inspector.multi_select_title": "🔗 多选操作 ({count} 个模型)",
"inspector.multi_select_hint": "已选中 {count} 个模型，可整体移动或执行 Bool Diff。",
"inspector.anchor_position": "⚓ 最低点锚点位置",
"inspector.anchor_hint": "所有选中模型的包围盒最小角坐标",
"label.min_x": "最低点 X",
"label.min_y": "最低点 Y",
"label.min_z": "最低点 Z",
"label.opacity": "整体透明度",
"common.display": "显示"
```

(Plus en-US translations for all above.)

- [ ] **Step 2: Replace Chinese in renderModelInspector() — the largest function**

Work through methodically: position labels, size labels, rotation, buttons, model-type-specific sections.

- [ ] **Step 3: Replace Chinese in renderWallBaffleInspector() and renderAcuModelInspector()**

Around lines 5092-5160 and 5162+.

- [ ] **Step 4: Replace Chinese in renderMultiSelectInspector()**

Multi-select anchor, opacity, Bool Diff controls.

- [ ] **Step 5: Verify and commit**

Click on each model type (box, wall, baffle, floor, ACU, custom) → verify inspector in both languages. Select two models → verify multi-select.

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract model and multi-select inspector strings"
```

---

## Task 6c: Extract Inspector Strings — Rack System (Slot, Equipment, Partition, CFD)

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (functions: `renderRackSlotInspector`, `renderRackMultiSlotInspector`, `renderRackEquipmentInspector`, `renderEquipmentCfdInspector`, `renderRackPartitionInspector`)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

The rack system has heavy Chinese content for U-slot mechanics.

- [ ] **Step 1: Add rack inspector keys to JSON files**

**zh-CN.json additions:**
```json
"rack.through_cavity": "贯通空腔",
"rack.residual_delete": "残余对向删除",
"rack.cavity_partition": "空腔边界隔板",
"rack.bottom_partition": "底部隔板",
"rack.top_partition": "顶部隔板",
"rack.non_continuous": "当前选择不连续",
"rack.extend_fan_server": "扩展当前 fan-server",
"rack.merge_slots": "合并选中的 slot",
"rack.equipment_type": "类型：",
"rack.equipment_name": "设备名称",
"btn.delete_equipment": "删除这个设备",
"rack.flow_rate": "风量",
"rack.power": "功耗",
"rack.fixed_temp_face": "固定温度面",
"rack.inlet_face": "入风面 (Inlet)",
"rack.outlet_face": "出风面 (Outlet)"
```

(Plus en-US translations.)

- [ ] **Step 2: Replace Chinese in all rack inspector functions**

Process each function: `renderRackSlotInspector` (~lines 5465-5542), `renderRackMultiSlotInspector` (~lines 5544-5640), `renderRackEquipmentInspector` (~lines 5650-5668), `renderEquipmentCfdInspector` (~lines 5670-5690), `renderRackPartitionInspector` (~lines 5692+).

- [ ] **Step 3: Verify and commit**

Load a project with rack models, click on rack → slot → equipment → verify all labels translate.

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract rack system inspector strings"
```

---

## Task 6d: Extract Inspector Strings — Patch, Physics, Bool Diff, STL Preview

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html` (patch rendering ~lines 5840-5928, Bool Diff UI, `renderStlPreviewRootInspector`, `renderStlPreviewModelInspector`, `renderStlPreviewRegionInspector`)
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

- [ ] **Step 1: Add patch, physics, Bool Diff, and STL preview keys to JSON files**

**zh-CN.json additions (patch inspector):**
```json
"inspector.patch_name": "区域名称",
"inspector.patch_type": "区域类型",
"label.circle_radius": "圆孔半径 R",
"label.projection_cx": "投影圆心 X",
"label.projection_cy": "投影圆心 Y",
"label.corner_x": "左下角 X",
"label.corner_y": "左下角 Y",
"label.coverage_w": "覆盖宽度 W",
"label.coverage_h": "覆盖高度 H",
"btn.fill_horizontal": "↔ 横向边界铺满",
"btn.fill_vertical": "↕ 纵向边界铺满",
"label.show_arrow": "显示箭头",
"label.vector_x": "向量 X",
"label.vector_y": "向量 Y",
"label.vector_z": "向量 Z",
"btn.reset_projection": "↺ 重置为当前面的默认投影",
"label.on": "开启",
"label.off": "关闭"
```

**ACU/baffle/floor physics:**
```json
"physics.acu_patch_title": "❄️ ACU Patch 物理属性",
"physics.patch_role": "Patch 角色",
"physics.wall": "壁面 (Wall)",
"physics.supply": "送风面 (Supply)",
"physics.return": "回风面 (Return)",
"physics.supply_temp": "送风温度 (°C)",
"physics.flow_rate": "风量 (m³/s)",
"physics.acu_bc_hint": "送风: flowRateInletVelocity + fixedValue T; 回风: flowRateOutletVelocity + zeroGradient T",
"physics.patch_cfd_title": "🔬 Patch CFD 属性",
"physics.baffle_type": "挡板类型",
"physics.wall_baffle": "内部挡板 wall baffle（保留两侧网格）",
"physics.perforated_baffle": "穿孔挡板 perforated wall baffle（保留两侧网格）",
"physics.porosity": "开孔率 (%)",
"physics.opening_title": "🔬 开口属性",
"physics.opening_type": "开口类型",
"physics.solid_wall": "实体墙 wall（单侧保留网格）",
"physics.wall_baffle_two": "内部挡板 wall baffle（保留两侧网格）",
"physics.perforated_two": "穿孔挡板 perforated wall baffle（保留两侧网格）",
"physics.perforated_hint": "穿孔挡板生成 cyclic master/slave patch 并施加 porousBafflePressure BC。",
"physics.heat_title": "🌡️ 发热属性 (可选)",
"physics.enable_fixed_temp": "启用固定温度",
"physics.surface_temp": "表面温度 (°C)"
```

**Bool Diff:**
```json
"bool.title": "Bool Diff",
"bool.status": "状态：",
"bool.generated": "已生成 {count} 个投影区域",
"bool.not_executed": "未执行",
"btn.show_cylinder": "👁 重新显示光柱并继续编辑",
"btn.execute_bool": "⚙ 执行 Bool Diff 运算",
"bool.2d_params": "2D 投影参数",
"bool.2d_params_safe": "2D 投影参数 (10%防撞墙已开启)",
"error.cylinder_invalid": "⛔ 光柱尺寸非法，已自动回夹到安全范围",
"error.boundary_intercept": "⛔ 触发 10% 物理边界安全拦截",
"error.value_out_of_range": "⛔ 数值越界已被拦截",
"bool.projection_vector": "🧭 投影向量",
"bool.diff_title": "⚙️ Bool Diff 布尔差集",
"bool.target": "被减对象",
"bool.tool": "减除对象",
"btn.swap_objects": "🔄 交换对象",
"label.keep_tool": "保留减除对象",
"bool.target_hint": "⚠️ 被减对象应为地板或挡板类型。",
"btn.execute_diff": "⚡ 执行 Bool Diff",
"bool.select_two_hint": "选中两个模型后，可以从被减对象中减去减除对象的重合区域",
"bool.target_type_hint": "⚠️ 被减对象应为地板或挡板类型（平面几何），才能执行 2D Bool Diff。"
```

**STL Preview:**
```json
"preview.memory_hint": "当前视图直接使用内存中的几何数据",
"preview.model_count": "模型数量",
"preview.return_modeling": "返回建模 View",
"preview.model_type": "模型类型",
"preview.export_filename": "导出文件名",
"preview.show_model": "显示该模型",
"preview.refresh": "刷新 Preview"
```

(Plus en-US translations for ALL of the above.)

- [ ] **Step 2: Replace Chinese in patch inspector code**

Around lines 5840-5928 — the patch property panel with circle/rect params, ACU physics, baffle physics, floor openings, heat properties. Use inline `t()` calls in template literals.

- [ ] **Step 3: Replace Chinese in Bool Diff UI sections**

Both the single-model Bool Diff (patch-level) and multi-select Bool Diff.

- [ ] **Step 4: Replace Chinese in STL Preview inspector functions**

`renderStlPreviewRootInspector` (~lines 6415-6436), `renderStlPreviewModelInspector` (~lines 6438-6459), `renderStlPreviewRegionInspector` (~line 6461+).

- [ ] **Step 5: Verify and commit**

Test patches on various model types, Bool Diff operations, and STL Preview mode.

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract patch, physics, Bool Diff, and STL preview strings"
```

---

## Task 7: Extract JavaScript Dynamic UI Strings — Miscellaneous Functions

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

Covers remaining JS strings: toolbar collapse button text, status messages, tooltip strings set via JS, `_appendDctwinLogLine()` messages, and other scattered Chinese strings NOT already handled in Tasks 5/6.

**Excludes:** `logOperation()` messages (handled in Task 7b), `console.warn()`/`console.error()` messages (developer-only, not translated), CSS/HTML comments (not user-visible).

- [ ] **Step 1: Find all remaining Chinese strings in JS**

```bash
grep -n -P '[\x{4e00}-\x{9fff}]' STL_generate.html | grep -v "data-i18n" | grep -v "^[0-9]*:.*\/\/" | grep -v "^[0-9]*:.*\/\*" | grep -v "logOperation"
```

Review the output, skip lines already processed. Common remaining categories:
- `_appendDctwinLogLine()` messages (user-visible in Terminal)
- Toolbar collapse button text toggle (`▶ 展开工具栏` / `▼`)
- Status messages in dynamic HTML
- Title attribute strings set via JS (`el.title = '...'`)

- [ ] **Step 2: Add remaining keys to JSON files and replace in code**

Process each category systematically. Add keys, add translations, replace in code.

- [ ] **Step 3: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract remaining misc JS strings to language dictionaries"
```

---

## Task 7b: Extract logOperation() Message Strings

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

There are ~157 `logOperation()` calls. These messages appear in the user-visible Terminal panel at the bottom of the UI and MUST be translated for consistency. The category labels (first argument like `'create'`, `'delete'`, `'modify'`) are already English and stay as-is. Only the human-readable message (second argument) needs translation.

- [ ] **Step 1: Extract all logOperation message strings**

```bash
grep -n "logOperation(" STL_generate.html | head -50
```

Pattern: `logOperation('category', 'human-readable message', ...)`

Only the second argument (message) needs `t()`. Example:

```javascript
// Before
logOperation('create', `已创建 ${model.kind} 模型: ${model.name}`, ...);

// After
logOperation('create', t('log.model_created', { kind: model.kind, name: model.name }), ...);
```

- [ ] **Step 2: Add log.* keys to JSON files**

Add `log.*` prefixed keys for all ~157 unique messages. Many will share patterns:

```json
"log.model_created": "已创建 {kind} 模型: {name}",
"log.model_deleted": "已删除模型: {name}",
"log.model_moved": "已移动模型: {name}",
"log.patch_added": "已添加 Patch: {name}",
"log.screenshot_saved": "📸 截图保存到 {path}"
```

(Plus en-US translations.)

- [ ] **Step 3: Replace logOperation messages systematically**

Work through all 157 calls. Many share identical message patterns, so the unique key count will be less than 157.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract logOperation terminal messages to language dictionaries"
```

---

## Task 8: Extract HTML Attribute Strings (title, placeholder)

**Files:**
- Modify: `STLGeneratorForOpenFoam/STL_generate.html`
- Modify: `STLGeneratorForOpenFoam/i18n/zh-CN.json`
- Modify: `STLGeneratorForOpenFoam/i18n/en-US.json`

- [ ] **Step 1: Find all Chinese text in HTML attributes**

```bash
grep -n -P 'title="[^"]*[\x{4e00}-\x{9fff}]' STL_generate.html
grep -n -P 'placeholder="[^"]*[\x{4e00}-\x{9fff}]' STL_generate.html
```

Key tooltips to extract:
- `title="请先在属性检查器中配置并验证 DCTwin 路径"` (mesh/solver tabs)
- `title="请先完成求解"` (postprocess tab)
- `title="拖拽调整左侧栏宽度"` (resize handles)
- `title="拖拽调整右侧栏宽度"`
- `title="拖拽调整工具栏高度"`
- `title="折叠/展开工具栏"`
- `title="折叠/展开左侧栏"` / `"折叠/展开右侧栏"`
- `title="拖动时面与面自动贴合"` (snap buttons)
- `title="拖动时边缘自动对齐"`
- `title="拖动时吸附到网格点..."`
- `title="拖动旋转视角，单击某个面切换到该法向视角"` (view cube)
- Mesh refinement label titles (`title="房间墙壁 (room_wall.stl)"`, etc.)

- [ ] **Step 2: Add tooltip keys to JSON files**

```json
"tooltip.configure_dctwin": "请先在属性检查器中配置并验证 DCTwin 路径",
"tooltip.complete_solve_first": "请先完成求解",
"tooltip.resize_left": "拖拽调整左侧栏宽度",
"tooltip.resize_right": "拖拽调整右侧栏宽度",
"tooltip.resize_toolbar": "拖拽调整工具栏高度",
"tooltip.toggle_toolbar": "折叠/展开工具栏",
"tooltip.toggle_left": "折叠/展开左侧栏",
"tooltip.toggle_right": "折叠/展开右侧栏",
"tooltip.face_snap": "拖动时面与面自动贴合",
"tooltip.edge_align": "拖动时边缘自动对齐",
"tooltip.grid_snap": "拖动时吸附到网格点（与面/边吸附互斥）",
"tooltip.view_cube": "拖动旋转视角，单击某个面切换到该法向视角",
"tooltip.room_wall_stl": "房间墙壁 (room_wall.stl)",
"tooltip.rack_stl": "机架外壳及内部构件 (merged_rack.stl)",
"tooltip.server_face": "fan-server / non-fan 设备面",
"tooltip.acu_stl": "ACU 设备 (merged_acu.stl)",
"tooltip.box_stl": "白模 (merged_box.stl)",
"tooltip.baffle_stl": "挡板和地板 (merged_baffle.stl, merged_floor_ceiling.stl)"
```

(Plus en-US translations.)

- [ ] **Step 3: Add data-i18n-title attributes to HTML elements**

```html
<!-- Before -->
<div id="left-resizer" class="resize-handle" title="拖拽调整左侧栏宽度"></div>

<!-- After -->
<div id="left-resizer" class="resize-handle" data-i18n-title="tooltip.resize_left" title="拖拽调整左侧栏宽度"></div>
```

- [ ] **Step 4: Verify tooltips**

Hover over various elements, verify tooltip language matches current locale.

- [ ] **Step 5: Commit**

```bash
git add STLGeneratorForOpenFoam/STL_generate.html STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract HTML title/placeholder attributes to language dictionaries"
```

---

## Task 9: Server.py i18n (Low Priority)

**Files:**
- Modify: `STLGeneratorForOpenFoam/server.py` (6 lines)

The server.py has 6 Chinese strings in log messages. These appear in the Operation Log terminal in the browser. Since they are server-side Python strings, the approach is different — use a simple dictionary lookup.

- [ ] **Step 1: Add a minimal i18n helper to server.py**

Near the top of server.py, after imports:

```python
import json, pathlib

_I18N_DIR = pathlib.Path(__file__).parent / "i18n"
_I18N_CACHE = {}

def _load_lang(locale: str) -> dict:
    if locale not in _I18N_CACHE:
        p = _I18N_DIR / f"{locale}.json"
        _I18N_CACHE[locale] = json.loads(p.read_text()) if p.exists() else {}
    return _I18N_CACHE[locale]

def st(key: str, locale: str = "zh-CN", **kwargs) -> str:
    """Server-side translation."""
    d = _load_lang(locale)
    fb = _load_lang("zh-CN")
    text = d.get(key) or fb.get(key) or key
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text
```

- [ ] **Step 2: Add server-specific keys to JSON files**

**zh-CN.json:**
```json
"server.start_command": "[server] 启动命令: {cmd}",
"server.start_failed": "[server] 启动失败: {error}",
"server.command": "[server] 命令: {cmd}",
"server.work_dir": "[server] 工作目录: {dir}",
"server.user_abort": "[server] ⚠️ 用户中断了任务"
```

**en-US.json:**
```json
"server.start_command": "[server] Starting command: {cmd}",
"server.start_failed": "[server] Start failed: {error}",
"server.command": "[server] Command: {cmd}",
"server.work_dir": "[server] Working directory: {dir}",
"server.user_abort": "[server] ⚠️ User aborted the task"
```

- [ ] **Step 3: Replace hardcoded strings in server.py**

```python
# Before (line 1303)
JOBS[job_id] = {"lines": [f"[server] 启动命令: {cmd_str}"], ...}

# After
JOBS[job_id] = {"lines": [st("server.start_command", cmd=cmd_str)], ...}
```

Note: server.py currently has no way to know the client's preferred language. For now, default to `zh-CN`. A future enhancement could pass `Accept-Language` header or a query param.

- [ ] **Step 4: Commit**

```bash
git add STLGeneratorForOpenFoam/server.py STLGeneratorForOpenFoam/i18n/
git commit -m "feat(i18n): extract server.py log strings to language dictionaries"
```

---

## Task 10: Final Integration Testing and Cleanup

**Files:**
- All modified files

- [ ] **Step 1: Full language switch test**

1. Start server: `cd STLGeneratorForOpenFoam && python server.py --case-dir ../dctwin/case/V5_CFD_Station --port 8001`
2. Open browser to `http://localhost:8001/STL_generate.html`
3. Load the test project: `Saved_proj/V5_CFD_Station_1773892919284.cfdproj`
4. Switch to English → systematically check every panel:
   - File bar buttons
   - Tab labels
   - Tree view (categories, root node, mode badge)
   - Click each model type → inspector labels
   - Mesh settings panel (all labels, hints, buttons)
   - Solver settings panel
   - Post-processing panel
   - Toolbar buttons and tooltips
   - Right panel inspector title
5. Switch back to Chinese → verify everything reverts
6. Refresh page → verify language persists

- [ ] **Step 2: Test edge cases**

- Language switch while mesh is running
- Language switch in post-processing mode
- Language switch with multi-select active
- Delete `localStorage` → verify fallback to zh-CN
- Block i18n JSON file loading (rename file) → verify Chinese fallback works

- [ ] **Step 3: Verify no Chinese leaks in English mode**

```javascript
// In browser console, after switching to en-US:
// Look for any remaining Chinese characters in the visible DOM
document.body.innerText.match(/[\u4e00-\u9fff]+/g)
```

Any remaining Chinese strings are either:
- Intentionally untranslated (e.g., inside input values set by user)
- Missing from the dictionary — add them

- [ ] **Step 4: Validate JSON files**

```bash
python3 -c "
import json
zh = json.load(open('STLGeneratorForOpenFoam/i18n/zh-CN.json'))
en = json.load(open('STLGeneratorForOpenFoam/i18n/en-US.json'))
zh_keys = set(k for k in zh if not k.startswith('_'))
en_keys = set(k for k in en if not k.startswith('_'))
missing = zh_keys - en_keys
extra = en_keys - zh_keys
print(f'zh-CN keys: {len(zh_keys)}')
print(f'en-US keys: {len(en_keys)}')
if missing: print(f'Missing in en-US: {missing}')
if extra: print(f'Extra in en-US: {extra}')
if not missing and not extra: print('✅ All keys match!')
"
```

- [ ] **Step 5: Final commit**

```bash
git add STLGeneratorForOpenFoam/
git commit -m "feat(i18n): complete i18n integration testing and cleanup"
```

---

## Summary

| Task | Scope | Approx. Strings |
| ---- | ----- | --------------- |
| 1 | i18n infrastructure + 10 test strings | 10 |
| 2 | Language selector UI | 0 |
| 3 | HTML static strings (all panels) | ~120 |
| 4 | JS alert/confirm messages | ~60 |
| 5 | JS tree view + inspector title strings | ~20 |
| 6a | Inspector: root, DCTwin config, bounds, room temp | ~30 |
| 6b | Inspector: model (box/wall/baffle/floor/ACU/custom), multi-select | ~80 |
| 6c | Inspector: rack system (slot, equipment, partition, CFD) | ~60 |
| 6d | Inspector: patch, physics, Bool Diff, STL preview | ~100 |
| 7 | JS misc strings (toolbar, status, _appendDctwinLogLine) | ~50 |
| 7b | logOperation() terminal messages | ~100 |
| 8 | HTML title/placeholder attributes | ~30 |
| 9 | server.py strings | 6 |
| 10 | Integration testing | 0 |
| **Total** | | **~670** |

Note: The ~670 unique dictionary keys may map to ~800+ individual string occurrences in the source (some keys are reused across multiple call sites, and some lines contain multiple strings that share a key like `common.display`).

### Dependencies

```text
Task 1 → Task 2 → Tasks 3-9 (parallel) → Task 10
```

Tasks 3 through 9 (including 6a-6d, 7, 7b, 8) can be executed in any order once Tasks 1-2 are complete, as they modify different sections of the JS/HTML code. **However**, all tasks append keys to the same two JSON files (`zh-CN.json`, `en-US.json`). If running tasks in parallel via separate agents, expect merge conflicts on these JSON files — they are easily resolvable since each task adds non-overlapping keys.
