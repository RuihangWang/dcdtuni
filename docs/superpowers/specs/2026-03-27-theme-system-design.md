# STL Generator 主题系统设计

**日期**: 2026-03-27
**范围**: 仅 UI 皮肤（CSS 颜色、字体、边框、按钮样式等 2D 界面元素）
**方案**: JSON 配置 + JS ThemeManager

---

## 1. 问题与目标

### 现状
- `web/STL_generate.html` 是一个 16,252 行的单体 HTML 文件
- 所有样式嵌入在 lines 19-440 的 `<style>` 块中
- `:root` 已定义 18 个 CSS 变量，被广泛使用（良好的基础）
- 但仍有 ~90-120 个 inline style 含硬编码色值
- 21 处 JS 动态设置颜色的代码
- 4 个 dialog 模板字符串中含硬编码颜色
- `web/theme/` 目录已存在但为空
- 当前固定为赛博朋克深色主题，无切换能力

### 目标
- 将主题配置提取为独立 JSON 文件，存放于 `web/theme/`
- 用户可通过 UI 下拉菜单在不同主题之间快速切换
- 主题选择持久化到 localStorage
- 提供至少 2 个主题：当前赛博朋克深色 + 一个浅色专业主题

---

## 2. 架构设计

### 2.1 目录结构

```
web/theme/
├── theme-loader.js              # ThemeManager 模块（~150 行）
├── themes.json                   # 主题清单（列出所有可用主题）
├── cyberpunk-dark.json           # 默认主题（当前外观）
└── light-professional.json       # 浅色专业主题
```

**`themes.json` 清单文件**：浏览器无法列出目录内容，因此需要一个清单文件声明可用主题：
```json
{
  "default": "cyberpunk-dark",
  "themes": [
    { "id": "cyberpunk-dark", "displayName": "🌙 赛博朋克" },
    { "id": "light-professional", "displayName": "☀️ 专业浅色" }
  ]
}
```
用户添加新主题时，只需创建 JSON 文件并在 `themes.json` 中添加一行。

### 2.2 主题 JSON Schema

每个主题是一个独立 JSON 文件，结构如下：

```json
{
  "name": "Cyberpunk Dark",
  "displayName": "赛博朋克",
  "version": "1.0",

  "colors": {
    "bg": "#1a1a22",
    "panel": "#1e1e28",
    "bgInput": "#161620",
    "bgPanel": "#1e1e28",
    "border": "#2e2e42",
    "borderBright": "#3a3a52",
    "text": "#bcccd6",
    "textDim": "#8899a6",
    "highlight": "#00ff88",
    "glow": "rgba(0,255,136,0.45)",
    "glowDim": "rgba(0,255,136,0.18)",
    "accent": "#00ff88",
    "accent2": "#ff0070",
    "glow2": "rgba(255,0,112,0.45)",
    "accent3": "#00d4ff",
    "danger": "#ff2244",
    "success": "#00ff88",
    "active": "#0a2a18"
  },

  "extendedColors": {
    "tabActiveBg": "#252540",
    "tabActiveText": "#8be9fd",
    "tabInactiveBg": "#1a1a2e",
    "tabInactiveText": "#666",
    "dialogOverlay": "rgba(0,0,0,0.72)",
    "dialogBg": "#282c34",
    "dialogBorder": "#555",
    "fileBrowserOverlay": "rgba(18,18,28,0.88)",
    "fileBrowserBg": "#1e1e28",
    "fileBrowserBorder": "rgba(0,255,136,0.25)",
    "hintColor": "#e5c07b",
    "errorColor": "#e06c75",
    "toggleOnBg": "#16825d",
    "runBtnActiveBg": "#2e7d32",
    "runBtnActiveText": "#fff",
    "runBtnDisabledBg": "#555",
    "runBtnDisabledText": "#999",
    "sectionHeaderCreate": "#56b6c2",
    "sectionHeaderRefine": "#c678dd",
    "sectionHeaderBlock": "#61afef",
    "sectionHeaderZone": "#00bcd4",
    "sectionHeaderLocation": "#e5c07b",
    "sectionHeaderSolver": "#c678dd",
    "sectionHeaderGravity": "#61afef",
    "sectionHeaderField": "#e5c07b",
    "sectionHeaderColorbar": "#c678dd",
    "sectionHeaderStreamline": "#56b6c2",
    "sectionHeaderDisplay": "#98c379",
    "logCatCreate": "#00ff88",
    "logCatDelete": "#ff2244",
    "logCatModify": "#ffb800",
    "logCatSelect": "#00d4ff",
    "logCatView": "#4a5a6a",
    "logCatFile": "#cc44ff",
    "logTime": "#3a4a58",
    "logContent": "#58687a"
  },

  "typography": {
    "fontPrimary": "'Share Tech Mono', 'Consolas', monospace",
    "fontHeading": "'Orbitron', monospace"
  },

  "scrollbar": {
    "thumbColor": "#00ff8840",
    "trackColor": "#161620"
  },

  "effects": {
    "scanlines": true,
    "glitch": true,
    "neonPulse": true,
    "clipPathCorners": true
  }
}
```

**设计决策**：
- `colors` 对应当前 `:root` 中已有的 CSS 变量（1:1 映射）
- `extendedColors` 是需要新增的 CSS 变量，用于替换散落的硬编码色值
- `effects` 控制赛博朋克特有效果的开关（扫描线、故障效果、霓虹脉冲、切角 clip-path）
- JSON key 使用 camelCase，映射到 CSS 变量时转为 `--kebab-case`

### 2.3 ThemeManager 模块

`web/theme/theme-loader.js` 核心逻辑：

```javascript
const ThemeManager = {
  _current: null,
  _cache: {},

  _manifest: null,

  // 初始化：加载清单 + 已保存的主题或默认主题
  async init() {
    const resp = await fetch('theme/themes.json');
    this._manifest = await resp.json();
    const saved = localStorage.getItem('stlgen-theme') || this._manifest.default;
    await this.loadTheme(saved);
    this._populateSelector();
  },

  // 填充 UI 下拉菜单
  _populateSelector() {
    const sel = document.getElementById('theme-selector');
    if (!sel) return;
    sel.innerHTML = '';
    for (const t of this._manifest.themes) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.displayName;
      opt.selected = t.id === this._current;
      sel.appendChild(opt);
    }
  },

  // 加载并应用主题
  async loadTheme(themeName) {
    if (!this._cache[themeName]) {
      const resp = await fetch(`theme/${themeName}.json`);
      this._cache[themeName] = await resp.json();
    }
    this._applyTheme(this._cache[themeName]);
    this._current = themeName;
    localStorage.setItem('stlgen-theme', themeName);
  },

  // 将 JSON 值写入 CSS 变量
  _applyTheme(theme) {
    const root = document.documentElement;
    // colors → --bg, --panel, etc.
    for (const [key, val] of Object.entries(theme.colors)) {
      root.style.setProperty(`--${this._toKebab(key)}`, val);
    }
    // extendedColors → --tab-active-bg, etc.
    for (const [key, val] of Object.entries(theme.extendedColors)) {
      root.style.setProperty(`--${this._toKebab(key)}`, val);
    }
    // typography
    root.style.setProperty('--font-primary', theme.typography.fontPrimary);
    root.style.setProperty('--font-heading', theme.typography.fontHeading);
    // scrollbar
    root.style.setProperty('--scrollbar-thumb', theme.scrollbar.thumbColor);
    root.style.setProperty('--scrollbar-track', theme.scrollbar.trackColor);
    // effects
    document.body.classList.toggle('no-scanlines', !theme.effects.scanlines);
    document.body.classList.toggle('no-glitch', !theme.effects.glitch);
    document.body.classList.toggle('no-neon-pulse', !theme.effects.neonPulse);
    document.body.classList.toggle('no-clip-corners', !theme.effects.clipPathCorners);
  },

  _toKebab(str) {
    return str.replace(/([A-Z])/g, '-$1').toLowerCase();
  },

  getCurrentTheme() { return this._current; }
};
```

### 2.4 CSS 改动

#### 2.4.1 新增 CSS 变量 fallback

在 `:root` 中为 `extendedColors` 添加默认值（确保不加载 theme JSON 时也能正常显示）：

```css
:root {
  /* 已有变量保持不变作为 fallback */
  --bg: #1a1a22;
  ...

  /* 新增 extended 变量 */
  --tab-active-bg: #252540;
  --tab-active-text: #8be9fd;
  --dialog-overlay: rgba(0,0,0,0.72);
  --dialog-bg: #282c34;
  ...

  /* typography */
  --font-primary: 'Share Tech Mono', 'Consolas', monospace;
  --font-heading: 'Orbitron', monospace;

  /* scrollbar */
  --scrollbar-thumb: #00ff8840;
  --scrollbar-track: #161620;
}
```

#### 2.4.2 效果开关 CSS class

```css
body.no-scanlines::after { display: none; }
body.no-glitch .glitch-effect { animation: none; }
body.no-neon-pulse .neon-pulse { animation: none; }
body.no-clip-corners [class*="btn"],
body.no-clip-corners .prop-group,
body.no-clip-corners .dialog-inner {
  clip-path: none;
  border-radius: 4px;
}
```

#### 2.4.3 替换硬编码颜色

将 CSS 中剩余的硬编码色值替换为 `var(--xxx)` 引用。例如：

```css
/* Before */
.log-cat-create { color: #00ff88; }
/* After */
.log-cat-create { color: var(--log-cat-create); }
```

### 2.5 JS 改动

#### 2.5.1 替换动态颜色设置

将 21 处 JS 硬编码颜色替换为 CSS 变量引用：

```javascript
// Before
el.style.backgroundColor = '#16825d';
// After
el.style.backgroundColor = 'var(--toggle-on-bg)';
```

#### 2.5.2 Dialog 模板字符串

将 dialog 模板中的硬编码颜色替换为 CSS 变量引用：

```javascript
// Before
`<div style="background:rgba(0,0,0,0.72)">`
// After
`<div style="background:var(--dialog-overlay)">`
```

### 2.6 UI 主题切换器

在工具栏的语言选择器 `#lang-selector` 旁边添加主题下拉菜单：

```html
<select id="theme-selector" onchange="ThemeManager.loadTheme(this.value)">
  <!-- 由 ThemeManager._populateSelector() 从 themes.json 动态填充 -->
</select>
```

样式与 `#lang-selector` 保持一致。选项列表从 `themes.json` 动态生成，用户添加新主题无需改 HTML。

### 2.7 服务端

**无需任何改动**。`server.py` 使用 `SimpleHTTPRequestHandler` 以 `web/` 为根目录，自动提供 `web/theme/` 下的静态文件。

---

## 3. 实施阶段

### Phase 1：基础设施（ThemeManager + JSON + 切换器）
- 创建 `theme-loader.js`
- 创建 `cyberpunk-dark.json`（从当前 `:root` 提取）
- 在 HTML 中引入 `theme-loader.js`
- 在 `:root` 中添加 `extendedColors` 的 fallback 默认值
- 添加效果开关 CSS class
- 添加 UI 主题切换下拉菜单
- **验证**：切换主题后所有 CSS 变量驱动的元素都正确变化

### Phase 2：清理 CSS 硬编码颜色
- 将 CSS `<style>` 块中所有硬编码色值替换为 `var(--xxx)`
- 主要涉及：log 类别颜色、scrollbar、prop-group 边框等
- **验证**：切换主题后 CSS 定义的元素颜色跟随变化

### Phase 3：清理 JS 硬编码颜色
- 替换 21 处 JS 动态颜色设置为 CSS 变量引用
- 替换 dialog 模板字符串中的硬编码颜色
- **验证**：切换主题后 dialog、tab 切换、按钮状态颜色跟随变化

### Phase 4：创建浅色主题
- 创建 `light-professional.json`
- 设计浅色配色方案（白/浅灰背景、深色文字、蓝色强调色）
- 禁用赛博朋克特效（scanlines: false, glitch: false, clipPathCorners: false）
- 使用专业字体（如 Inter, system-ui）
- **验证**：完整的浅色主题可用且视觉一致

---

## 4. 约束与注意事项

1. **向后兼容**：`:root` 中保留所有 CSS 变量的默认值作为 fallback，即使 `theme-loader.js` 加载失败也不影响现有外观
2. **不涉及 Three.js 场景**：3D 视口背景、灯光、材质颜色不在主题范围内
3. **不涉及 PALETTE / equipment colors**：面片颜色、设备前后面颜色是业务数据，不随主题变化
4. **性能**：JSON 文件很小（~2KB），fetch 一次后缓存，切换主题是纯 DOM 操作（setProperty），无性能问题
5. **FOUC 防护**：ThemeManager.init() 在 DOMContentLoaded 时立即执行，配合 `:root` fallback 值，闪烁可忽略
6. **单体 HTML 约束**：`theme-loader.js` 是唯一新增的外部 JS 文件，保持项目简洁

---

## 5. 成功标准

- [ ] 用户可通过工具栏下拉菜单在至少 2 个主题之间切换
- [ ] 切换后所有 2D UI 元素（面板、按钮、文字、边框、dialog、tab、log）颜色正确变化
- [ ] 主题选择刷新页面后保持
- [ ] 当前赛博朋克外观在默认主题下 100% 还原
- [ ] 无 theme-loader.js 时页面正常显示（fallback）
- [ ] 用户可通过创建新的 JSON 文件 + 在 themes.json 中添加一行来添加自定义主题
