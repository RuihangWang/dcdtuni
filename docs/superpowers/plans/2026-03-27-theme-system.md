# Theme System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract UI theme configuration from the monolithic HTML into switchable JSON files in `web/theme/`, enabling users to swap between visual themes via a toolbar dropdown.

**Architecture:** ThemeManager (JS module) loads theme JSON files, applies values as CSS custom properties on `:root`, and toggles effect classes on `<body>`. A `themes.json` manifest declares available themes. The HTML retains current CSS variable values as fallbacks.

**Tech Stack:** Vanilla JS, CSS custom properties, JSON config files. No build system.

**Spec:** `docs/superpowers/specs/2026-03-27-theme-system-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `web/theme/themes.json` | Manifest listing available themes and default |
| Create | `web/theme/cyberpunk-dark.json` | Default theme (current visual appearance) |
| Create | `web/theme/theme-loader.js` | ThemeManager module: load, apply, cache, persist themes |
| Create | `web/theme/light-professional.json` | Alternative light theme |
| Modify | `web/STL_generate.html:23-45` | Add extended CSS variable fallbacks to `:root` |
| Modify | `web/STL_generate.html:62-74` | Replace hardcoded font-family/scrollbar with `var()` |
| Modify | `web/STL_generate.html:356-362,439` | Replace `.log-cat-*` hardcoded colors with `var()` |
| Modify | `web/STL_generate.html:66-69` | Add `.no-scanlines` override for `body::after` |
| Modify | `web/STL_generate.html:872` | Add `#theme-selector` next to `#lang-selector` |
| Modify | `web/STL_generate.html:999` | Add `<script src="theme/theme-loader.js">` before main script |
| Modify | `web/STL_generate.html:3035` | Call `ThemeManager.init()` in DOMContentLoaded |
| Modify | `web/STL_generate.html` (scattered) | Replace ~21 JS dynamic color assignments with `var()` |
| Modify | `web/STL_generate.html` (scattered) | Replace dialog template hardcoded colors with `var()` |

---

## Task 1: Create theme manifest and default theme JSON

**Files:**
- Create: `web/theme/themes.json`
- Create: `web/theme/cyberpunk-dark.json`

- [ ] **Step 1: Create `themes.json`**

```json
{
  "default": "cyberpunk-dark",
  "themes": [
    { "id": "cyberpunk-dark", "displayName": "\ud83c\udf19 \u8d5b\u535a\u670b\u514b" },
    { "id": "light-professional", "displayName": "\u2600\ufe0f \u4e13\u4e1a\u6d45\u8272" }
  ]
}
```

- [ ] **Step 2: Create `cyberpunk-dark.json`**

This is a 1:1 extraction of all current CSS variable values and hardcoded UI colors:

```json
{
  "name": "Cyberpunk Dark",
  "displayName": "\ud83c\udf19 \u8d5b\u535a\u670b\u514b",
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
    "tabActiveBorder": "#8be9fd",
    "tabInactiveBg": "#1a1a2e",
    "tabInactiveText": "#666",
    "dialogOverlay": "rgba(0,0,0,0.72)",
    "dialogBg": "#282c34",
    "dialogBorder": "#555",
    "fileBrowserOverlay": "rgba(18,18,28,0.88)",
    "fileBrowserBg": "#1e1e28",
    "fileBrowserBorder": "rgba(0,255,136,0.25)",
    "selectionBg": "rgba(0,255,136,0.15)",
    "selectionBgAlt": "rgba(77,170,252,0.15)",
    "hoverBg": "rgba(0,255,136,0.08)",
    "resizeHandleHover": "rgba(86,182,194,0.3)",
    "resizeHandleActive": "rgba(86,182,194,0.6)",
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
    "logCatDctwin": "#00ff88",
    "logTime": "#3a4a58",
    "logContent": "#58687a",
    "btnPrimary": "#61afef",
    "btnPrimaryText": "#1e1e2e",
    "pathInputText": "#56b6c2",
    "monitorTabActiveBg": "#2a3a4a",
    "monitorTabActiveBorder": "#4a6a8a",
    "monitorTabActiveText": "#8be9fd",
    "monitorTabInactiveBg": "#1a2a3a",
    "monitorTabInactiveBorder": "#333",
    "monitorTabInactiveText": "#888"
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

- [ ] **Step 3: Commit**

```bash
git add web/theme/themes.json web/theme/cyberpunk-dark.json
git commit -m "feat(theme): add theme manifest and default cyberpunk-dark theme JSON"
```

---

## Task 2: Create ThemeManager module

**Files:**
- Create: `web/theme/theme-loader.js`

- [ ] **Step 1: Write `theme-loader.js`**

```javascript
/**
 * ThemeManager — loads theme JSON files and applies them as CSS custom properties.
 *
 * Usage:
 *   <script src="theme/theme-loader.js"></script>
 *   Then call ThemeManager.init() in DOMContentLoaded.
 */
const ThemeManager = {
  _current: null,
  _cache: {},
  _manifest: null,

  /**
   * Initialize: load manifest, restore saved theme or default, populate UI selector.
   */
  async init() {
    try {
      const resp = await fetch('theme/themes.json');
      if (!resp.ok) throw new Error('Failed to load themes.json');
      this._manifest = await resp.json();
    } catch (e) {
      console.warn('[ThemeManager] Could not load theme manifest, using CSS fallbacks.', e);
      return;
    }
    const saved = localStorage.getItem('stlgen-theme');
    const themeName = (saved && this._manifest.themes.some(t => t.id === saved))
      ? saved
      : this._manifest.default;
    await this.loadTheme(themeName);
    this._populateSelector();
  },

  /**
   * Load a theme by name, apply it, cache it, persist choice.
   */
  async loadTheme(themeName) {
    if (!this._cache[themeName]) {
      try {
        const resp = await fetch(`theme/${themeName}.json`);
        if (!resp.ok) throw new Error(`Theme ${themeName} not found`);
        this._cache[themeName] = await resp.json();
      } catch (e) {
        console.error('[ThemeManager] Failed to load theme:', themeName, e);
        return;
      }
    }
    this._applyTheme(this._cache[themeName]);
    this._current = themeName;
    localStorage.setItem('stlgen-theme', themeName);
    // Update selector if it exists
    const sel = document.getElementById('theme-selector');
    if (sel) sel.value = themeName;
  },

  /**
   * Apply theme object: set CSS custom properties + toggle effect classes.
   */
  _applyTheme(theme) {
    const root = document.documentElement;

    // colors → --bg, --panel, etc.
    if (theme.colors) {
      for (const [key, val] of Object.entries(theme.colors)) {
        root.style.setProperty(`--${this._toKebab(key)}`, val);
      }
    }

    // extendedColors → --tab-active-bg, etc.
    if (theme.extendedColors) {
      for (const [key, val] of Object.entries(theme.extendedColors)) {
        root.style.setProperty(`--${this._toKebab(key)}`, val);
      }
    }

    // typography
    if (theme.typography) {
      if (theme.typography.fontPrimary) {
        root.style.setProperty('--font-primary', theme.typography.fontPrimary);
      }
      if (theme.typography.fontHeading) {
        root.style.setProperty('--font-heading', theme.typography.fontHeading);
      }
    }

    // scrollbar
    if (theme.scrollbar) {
      if (theme.scrollbar.thumbColor) {
        root.style.setProperty('--scrollbar-thumb', theme.scrollbar.thumbColor);
      }
      if (theme.scrollbar.trackColor) {
        root.style.setProperty('--scrollbar-track', theme.scrollbar.trackColor);
      }
    }

    // effects — toggle body CSS classes
    if (theme.effects) {
      document.body.classList.toggle('no-scanlines', !theme.effects.scanlines);
      document.body.classList.toggle('no-glitch', !theme.effects.glitch);
      document.body.classList.toggle('no-neon-pulse', !theme.effects.neonPulse);
      document.body.classList.toggle('no-clip-corners', !theme.effects.clipPathCorners);
    }
  },

  /**
   * Populate the #theme-selector dropdown from manifest.
   */
  _populateSelector() {
    const sel = document.getElementById('theme-selector');
    if (!sel) return;
    sel.innerHTML = '';
    for (const t of this._manifest.themes) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.displayName;
      opt.selected = (t.id === this._current);
      sel.appendChild(opt);
    }
  },

  /**
   * Convert camelCase to kebab-case: "bgInput" → "bg-input"
   */
  _toKebab(str) {
    return str.replace(/([A-Z])/g, '-$1').toLowerCase();
  },

  /**
   * Get current theme name.
   */
  getCurrentTheme() {
    return this._current;
  }
};
```

- [ ] **Step 2: Commit**

```bash
git add web/theme/theme-loader.js
git commit -m "feat(theme): add ThemeManager module for loading and applying theme JSON"
```

---

## Task 3: Add extended CSS variables and effect toggle classes to HTML

**Files:**
- Modify: `web/STL_generate.html:23-45` (`:root` block)
- Modify: `web/STL_generate.html:62` (`body` font-family)
- Modify: `web/STL_generate.html:66-69` (`body::after` scanlines)
- Modify: `web/STL_generate.html:72-74` (scrollbar)

- [ ] **Step 1: Extend `:root` with new CSS variable fallbacks**

Add after existing variables in the `:root` block (after line ~45, before the closing `}`):

```css
  /* --- extended theme variables (fallback defaults) --- */
  --font-primary: 'Share Tech Mono', 'Consolas', monospace;
  --font-heading: 'Orbitron', monospace;
  --scrollbar-thumb: #00ff8840;
  --scrollbar-track: #161620;

  --tab-active-bg: #252540;
  --tab-active-text: #8be9fd;
  --tab-active-border: #8be9fd;
  --tab-inactive-bg: #1a1a2e;
  --tab-inactive-text: #666;

  --dialog-overlay: rgba(0,0,0,0.72);
  --dialog-bg: #282c34;
  --dialog-border: #555;
  --file-browser-overlay: rgba(18,18,28,0.88);
  --file-browser-bg: #1e1e28;
  --file-browser-border: rgba(0,255,136,0.25);

  --selection-bg: rgba(0,255,136,0.15);
  --selection-bg-alt: rgba(77,170,252,0.15);
  --hover-bg: rgba(0,255,136,0.08);
  --resize-handle-hover: rgba(86,182,194,0.3);
  --resize-handle-active: rgba(86,182,194,0.6);

  --hint-color: #e5c07b;
  --error-color: #e06c75;
  --toggle-on-bg: #16825d;

  --run-btn-active-bg: #2e7d32;
  --run-btn-active-text: #fff;
  --run-btn-disabled-bg: #555;
  --run-btn-disabled-text: #999;

  --section-header-create: #56b6c2;
  --section-header-refine: #c678dd;
  --section-header-block: #61afef;
  --section-header-zone: #00bcd4;
  --section-header-location: #e5c07b;
  --section-header-solver: #c678dd;
  --section-header-gravity: #61afef;
  --section-header-field: #e5c07b;
  --section-header-colorbar: #c678dd;
  --section-header-streamline: #56b6c2;
  --section-header-display: #98c379;

  --log-cat-create: #00ff88;
  --log-cat-delete: #ff2244;
  --log-cat-modify: #ffb800;
  --log-cat-select: #00d4ff;
  --log-cat-view: #4a5a6a;
  --log-cat-file: #cc44ff;
  --log-cat-dctwin: #00ff88;
  --log-time: #3a4a58;
  --log-content: #58687a;

  --btn-primary: #61afef;
  --btn-primary-text: #1e1e2e;
  --path-input-text: #56b6c2;

  --monitor-tab-active-bg: #2a3a4a;
  --monitor-tab-active-border: #4a6a8a;
  --monitor-tab-active-text: #8be9fd;
  --monitor-tab-inactive-bg: #1a2a3a;
  --monitor-tab-inactive-border: #333;
  --monitor-tab-inactive-text: #888;
```

- [ ] **Step 2: Replace `body` font-family with variable**

At line ~62, change:
```css
/* Before */
body { font-family: 'Share Tech Mono', 'Consolas', monospace; ... }
/* After */
body { font-family: var(--font-primary); ... }
```

- [ ] **Step 3: Replace scrollbar colors with variables**

At lines ~72-74, change:
```css
/* Before */
::-webkit-scrollbar-thumb { background: #00ff8840; }
::-webkit-scrollbar-track { background: #161620; }
/* After */
::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); }
::-webkit-scrollbar-track { background: var(--scrollbar-track); }
```

- [ ] **Step 4: Add effect toggle CSS classes**

Add after the `body::after` rule (after line ~69):

```css
/* Theme effect toggles */
body.no-scanlines::after { display: none !important; }
body.no-clip-corners .btn,
body.no-clip-corners .file-btn,
body.no-clip-corners .tool-btn,
body.no-clip-corners .node-label,
body.no-clip-corners .node-expander,
body.no-clip-corners .node-action-btn,
body.no-clip-corners .badge,
body.no-clip-corners .prop-row input,
body.no-clip-corners .pp-btn,
body.no-clip-corners .pp-btn-sm {
  clip-path: none !important;
  border-radius: 4px;
}
```

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(theme): add extended CSS variable fallbacks and effect toggle classes"
```

---

## Task 4: Add theme selector UI and script tag to HTML

**Files:**
- Modify: `web/STL_generate.html:872` (near `#lang-selector`)
- Modify: `web/STL_generate.html:999` (before main `<script>`)
- Modify: `web/STL_generate.html:3035` (DOMContentLoaded)

- [ ] **Step 1: Add `<script>` tag for theme-loader.js**

Insert before the main inline `<script>` tag at line ~999:

```html
<script src="theme/theme-loader.js"></script>
```

- [ ] **Step 2: Add theme selector dropdown next to language selector**

Near line ~872, after the `#lang-selector` `</select>` closing tag, add:

```html
<select id="theme-selector" onchange="ThemeManager.loadTheme(this.value)"
  style="margin-left:6px;background:var(--bg-input);color:var(--text-dim);border:1px solid var(--border);
    padding:2px 6px;font-size:0.7rem;font-family:var(--font-primary);cursor:pointer;">
  <!-- populated dynamically by ThemeManager -->
</select>
```

- [ ] **Step 3: Call `ThemeManager.init()` in DOMContentLoaded**

At line ~3035, inside the `DOMContentLoaded` handler, add at the beginning:

```javascript
ThemeManager.init();
```

- [ ] **Step 4: Verify baseline**

Open `http://localhost:8000/STL_generate.html` in browser. Verify:
- Theme dropdown appears next to language selector
- Default "Cyberpunk Dark" is selected
- Current visual appearance is unchanged (CSS fallbacks + theme JSON should produce identical look)
- Console shows no errors

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "feat(theme): integrate ThemeManager script and theme selector UI"
```

---

## Task 5: Replace CSS hardcoded colors with `var()` references

**Files:**
- Modify: `web/STL_generate.html:356-362,439` (`.log-cat-*` rules)
- Modify: `web/STL_generate.html` (scattered font-family, clip-path-related, other hardcoded colors in CSS)

- [ ] **Step 1: Replace `.log-cat-*` colors**

At lines ~356-362 and ~439:

```css
/* Before */
.log-cat-create { color: #00ff88; }
.log-cat-delete { color: #ff2244; }
.log-cat-modify { color: #ffb800; }
.log-cat-select { color: #00d4ff; }
.log-cat-view { color: #4a5a6a; }
.log-cat-file { color: #cc44ff; }
.log-cat-drag { color: #00ff88; opacity: 0.7; }
/* ... */
.log-cat-dctwin { color: #00ff88; }

/* After */
.log-cat-create { color: var(--log-cat-create); }
.log-cat-delete { color: var(--log-cat-delete); }
.log-cat-modify { color: var(--log-cat-modify); }
.log-cat-select { color: var(--log-cat-select); }
.log-cat-view { color: var(--log-cat-view); }
.log-cat-file { color: var(--log-cat-file); }
.log-cat-drag { color: var(--log-cat-create); opacity: 0.7; }
/* ... */
.log-cat-dctwin { color: var(--log-cat-dctwin); }
```

- [ ] **Step 2: Replace other scattered CSS font-family references**

Search for `font-family:` in the `<style>` block. Replace occurrences of `'Share Tech Mono'` with `var(--font-primary)` and `'Orbitron'` with `var(--font-heading)` where they appear in class selectors (e.g., `.file-btn`, `.tool-btn`, `.tool-inline`, `#toolbar-collapse-btn`).

```css
/* Before (various locations) */
font-family: 'Share Tech Mono', 'Consolas', monospace;
/* After */
font-family: var(--font-primary);

/* Before */
font-family: 'Orbitron', monospace;
/* After */
font-family: var(--font-heading);
```

- [ ] **Step 3: Replace `.log-time` and `.log-content` colors**

Find the CSS rules for `.log-time` and `.log-content` and replace their hardcoded color values:

```css
/* Before */
.log-time { color: #3a4a58; }
.log-content { color: #58687a; }
/* After */
.log-time { color: var(--log-time); }
.log-content { color: var(--log-content); }
```

- [ ] **Step 4: Verify**

Reload page, verify log panel colors, fonts, and all CSS-styled elements look unchanged.

- [ ] **Step 5: Commit**

```bash
git add web/STL_generate.html
git commit -m "refactor(theme): replace CSS hardcoded colors and fonts with var() references"
```

---

## Task 6: Replace JS dynamic color assignments with `var()` references

**Files:**
- Modify: `web/STL_generate.html` at lines ~2771-2776, ~9065, ~9192-9193, ~11529-11540, ~11626-11648, ~11683, ~11728-11729, ~12025-12031, ~14209-14213, ~14714-14720

- [ ] **Step 1: STL Preview button toggle (lines ~2771-2776)**

```javascript
// Before
btn.style.background = '#16825d';
btn.style.color = '#fff';
// After
btn.style.background = 'var(--toggle-on-bg)';
btn.style.color = 'var(--run-btn-active-text)';
```

- [ ] **Step 2: Load Project dialog tab colors (line ~9065)**

```javascript
// Before
btn.style.color = btn.dataset.tab === activeTab ? 'var(--accent, #61afef)' : '#888';
// After
btn.style.color = btn.dataset.tab === activeTab ? 'var(--tab-active-text)' : 'var(--tab-inactive-text)';
```

- [ ] **Step 3: Load Project dialog row selection (lines ~9192-9193)**

```javascript
// Before
el.style.background = 'rgba(77,170,252,0.15)';
// After
el.style.background = 'var(--selection-bg-alt)';
```

- [ ] **Step 4: File browser hover effects (lines ~11529-11540)**

```javascript
// Before (in template strings)
onmouseenter="this.style.background='rgba(0,255,136,0.08)'"
// After
onmouseenter="this.style.background='var(--hover-bg)'"
```

Apply the same pattern for all 4 occurrences (column headers at ~11529/11531 and directory rows at ~11539/11540).

- [ ] **Step 5: File browser resize handle (lines ~11626-11648, ~11683)**

```javascript
// Before
const hoverColor = 'rgba(86,182,194,0.3)';
const activeColor = 'rgba(86,182,194,0.6)';
// After
const hoverColor = 'var(--resize-handle-hover)';
const activeColor = 'var(--resize-handle-active)';
```

- [ ] **Step 6: File browser row selection (lines ~11728-11729)**

```javascript
// Before
el.style.background = 'rgba(0,255,136,0.15)';
// After
el.style.background = 'var(--selection-bg)';
```

- [ ] **Step 7: Run/Solve button states (lines ~12025-12031)**

```javascript
// Before (mesh ready)
runBtn.style.background = '#2e7d32';
runBtn.style.color = '#fff';
// After
runBtn.style.background = 'var(--run-btn-active-bg)';
runBtn.style.color = 'var(--run-btn-active-text)';

// Before (mesh not ready)
runBtn.style.background = '#555';
runBtn.style.color = '#999';
// After
runBtn.style.background = 'var(--run-btn-disabled-bg)';
runBtn.style.color = 'var(--run-btn-disabled-text)';
```

- [ ] **Step 8: Inspector/Postprocess tab toggle (lines ~14209-14213)**

```javascript
// Before (active tab)
inspBtn.style.background = '#252540';
inspBtn.style.color = '#8be9fd';
inspBtn.style.borderBottom = '2px solid #8be9fd';
// After
inspBtn.style.background = 'var(--tab-active-bg)';
inspBtn.style.color = 'var(--tab-active-text)';
inspBtn.style.borderBottom = '2px solid var(--tab-active-border)';

// Before (inactive tab)
mpBtn.style.background = '#1a1a2e';
mpBtn.style.color = '#666';
mpBtn.style.borderBottom = '2px solid transparent';
// After
mpBtn.style.background = 'var(--tab-inactive-bg)';
mpBtn.style.color = 'var(--tab-inactive-text)';
mpBtn.style.borderBottom = '2px solid transparent';
```

Apply the same for the reverse case (mesh preview active, inspector inactive).

- [ ] **Step 9: Solver monitor tab toggle (lines ~14714-14720)**

```javascript
// Before (inactive)
tabs[j].style.background = '#1a2a3a';
tabs[j].style.borderColor = '#333';
tabs[j].style.color = '#888';
// After
tabs[j].style.background = 'var(--monitor-tab-inactive-bg)';
tabs[j].style.borderColor = 'var(--monitor-tab-inactive-border)';
tabs[j].style.color = 'var(--monitor-tab-inactive-text)';

// Before (active)
this.style.background = '#2a3a4a';
this.style.borderColor = '#4a6a8a';
this.style.color = '#8be9fd';
// After
this.style.background = 'var(--monitor-tab-active-bg)';
this.style.borderColor = 'var(--monitor-tab-active-border)';
this.style.color = 'var(--monitor-tab-active-text)';
```

- [ ] **Step 10: Verify**

Reload page. Test:
- Click tabs in right panel (Inspector / Mesh Preview) — colors should toggle correctly
- Open Load Project dialog — tab switching should work
- Open File Browser dialog — hover and selection should show correct colors
- Click mesh/solve buttons — state colors should reflect ready/not-ready

- [ ] **Step 11: Commit**

```bash
git add web/STL_generate.html
git commit -m "refactor(theme): replace JS dynamic color assignments with CSS variable references"
```

---

## Task 7: Replace dialog template hardcoded colors with `var()` references

**Files:**
- Modify: `web/STL_generate.html` at lines ~9095-9159, ~11562-11589, ~12249-12267, ~13878-13879

- [ ] **Step 1: Load Project dialog template (lines ~9095-9159)**

In the dialog template string, replace:

```javascript
// Before
`background:#282c34;`
// After
`background:var(--dialog-bg);`

// Before
`color:#56b6c2;`
// After
`color:var(--path-input-text);`
```

- [ ] **Step 2: File Browser dialog template (lines ~11562-11589)**

```javascript
// Before
`background:#1e1e28;`           // dialog body
`border:1px solid rgba(0,255,136,0.25);`  // dialog border
`background:#161620;`           // inner body
`color:#56b6c2;`               // path input text
// After
`background:var(--file-browser-bg);`
`border:1px solid var(--file-browser-border);`
`background:var(--bg-input);`
`color:var(--path-input-text);`
```

Also replace the overlay background:
```javascript
// Before
`background:rgba(18,18,28,0.88);`
// After
`background:var(--file-browser-overlay);`
```

- [ ] **Step 3: Colorbar config widget (lines ~12249-12267)**

```javascript
// Before (multiple occurrences)
`background:#161620;`
// After
`background:var(--bg-input);`
```

- [ ] **Step 4: Slice dialog buttons (lines ~13878-13879)**

```javascript
// Before
`background:#61afef;`  `color:#1e1e2e;`
// After
`background:var(--btn-primary);`  `color:var(--btn-primary-text);`
```

- [ ] **Step 5: Search for any remaining hardcoded colors in template strings**

Search the JS section for remaining patterns like `color:#`, `background:#`, `border.*#` inside template literals (backtick strings) and replace with appropriate CSS variable references. Use the `extendedColors` mapping to find the right variable.

- [ ] **Step 6: Verify**

Reload page. Open each dialog type and verify colors are correct:
- Load Project dialog
- File Browser dialog
- Colorbar popup
- Any slice/partition dialogs

- [ ] **Step 7: Commit**

```bash
git add web/STL_generate.html
git commit -m "refactor(theme): replace dialog template hardcoded colors with CSS variable references"
```

---

## Task 8: Replace inline style hardcoded section header colors

**Files:**
- Modify: `web/STL_generate.html` (scattered inline `style="color: #56b6c2"` etc. in HTML template strings for mesh/solver/postprocess section headers)

- [ ] **Step 1: Find and replace section header colors in JS template strings**

These are in the functions that build the mesh settings panel, solver settings panel, and postprocess panel. Each section header has an inline style like `style="color: #56b6c2"`.

Replace each with the corresponding CSS variable:

| Hardcoded | Variable | Section |
|-----------|----------|---------|
| `#56b6c2` | `var(--section-header-create)` | Global params / Streamlines |
| `#c678dd` | `var(--section-header-refine)` | Refinement / Solver / Colorbar |
| `#61afef` | `var(--section-header-block)` | BlockMesh / Gravity |
| `#00bcd4` | `var(--section-header-zone)` | Refinement zones |
| `#e5c07b` | `var(--section-header-location)` | locationInMesh / Field render |
| `#98c379` | `var(--section-header-display)` | Display options |

Search pattern: `style="[^"]*color:\s*#(56b6c2|c678dd|61afef|00bcd4|e5c07b|98c379)` in the file.

- [ ] **Step 2: Replace hint/warning color inline styles**

Search for `color:#e5c07b` and `color: #e5c07b` in inline styles and replace with `color:var(--hint-color)`.

Search for `color:#e06c75` and replace with `color:var(--error-color)`.

- [ ] **Step 3: Verify**

Reload page. Open mesh settings panel, solver settings panel, and postprocess panel. Verify section headers have correct colors.

- [ ] **Step 4: Commit**

```bash
git add web/STL_generate.html
git commit -m "refactor(theme): replace inline section header and hint colors with CSS variables"
```

---

## Task 9: Create light professional theme

**Files:**
- Create: `web/theme/light-professional.json`

- [ ] **Step 1: Create `light-professional.json`**

```json
{
  "name": "Light Professional",
  "displayName": "\u2600\ufe0f \u4e13\u4e1a\u6d45\u8272",
  "version": "1.0",

  "colors": {
    "bg": "#f5f5f5",
    "panel": "#ffffff",
    "bgInput": "#e8e8e8",
    "bgPanel": "#ffffff",
    "border": "#d0d0d0",
    "borderBright": "#b0b0b0",
    "text": "#333333",
    "textDim": "#777777",
    "highlight": "#0066cc",
    "glow": "rgba(0,102,204,0.25)",
    "glowDim": "rgba(0,102,204,0.10)",
    "accent": "#0066cc",
    "accent2": "#cc3366",
    "glow2": "rgba(204,51,102,0.25)",
    "accent3": "#0099cc",
    "danger": "#cc2244",
    "success": "#22883a",
    "active": "#e0f0ff"
  },

  "extendedColors": {
    "tabActiveBg": "#ffffff",
    "tabActiveText": "#0066cc",
    "tabActiveBorder": "#0066cc",
    "tabInactiveBg": "#f0f0f0",
    "tabInactiveText": "#999999",
    "dialogOverlay": "rgba(0,0,0,0.4)",
    "dialogBg": "#ffffff",
    "dialogBorder": "#d0d0d0",
    "fileBrowserOverlay": "rgba(0,0,0,0.35)",
    "fileBrowserBg": "#ffffff",
    "fileBrowserBorder": "#d0d0d0",
    "selectionBg": "rgba(0,102,204,0.12)",
    "selectionBgAlt": "rgba(0,102,204,0.08)",
    "hoverBg": "rgba(0,102,204,0.06)",
    "resizeHandleHover": "rgba(0,102,204,0.2)",
    "resizeHandleActive": "rgba(0,102,204,0.4)",
    "hintColor": "#b8860b",
    "errorColor": "#cc2244",
    "toggleOnBg": "#22883a",
    "runBtnActiveBg": "#22883a",
    "runBtnActiveText": "#ffffff",
    "runBtnDisabledBg": "#cccccc",
    "runBtnDisabledText": "#888888",
    "sectionHeaderCreate": "#0099aa",
    "sectionHeaderRefine": "#8833aa",
    "sectionHeaderBlock": "#336699",
    "sectionHeaderZone": "#007788",
    "sectionHeaderLocation": "#997700",
    "sectionHeaderSolver": "#8833aa",
    "sectionHeaderGravity": "#336699",
    "sectionHeaderField": "#997700",
    "sectionHeaderColorbar": "#8833aa",
    "sectionHeaderStreamline": "#0099aa",
    "sectionHeaderDisplay": "#338844",
    "logCatCreate": "#22883a",
    "logCatDelete": "#cc2244",
    "logCatModify": "#cc8800",
    "logCatSelect": "#0088bb",
    "logCatView": "#999999",
    "logCatFile": "#8833aa",
    "logCatDctwin": "#22883a",
    "logTime": "#aaaaaa",
    "logContent": "#666666",
    "btnPrimary": "#0066cc",
    "btnPrimaryText": "#ffffff",
    "pathInputText": "#0099aa",
    "monitorTabActiveBg": "#ffffff",
    "monitorTabActiveBorder": "#0066cc",
    "monitorTabActiveText": "#0066cc",
    "monitorTabInactiveBg": "#f0f0f0",
    "monitorTabInactiveBorder": "#d0d0d0",
    "monitorTabInactiveText": "#999999"
  },

  "typography": {
    "fontPrimary": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif",
    "fontHeading": "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif"
  },

  "scrollbar": {
    "thumbColor": "rgba(0,0,0,0.2)",
    "trackColor": "#f0f0f0"
  },

  "effects": {
    "scanlines": false,
    "glitch": false,
    "neonPulse": false,
    "clipPathCorners": false
  }
}
```

- [ ] **Step 2: Add Inter font import to HTML**

In the `<head>` section of the HTML (near the existing Google Fonts link for Orbitron/Share Tech Mono), add:

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&display=swap" rel="stylesheet">
```

- [ ] **Step 3: Verify theme switching**

1. Reload page
2. Select "Professional Light" from theme dropdown
3. Verify:
   - Background changes to light gray/white
   - Text changes to dark
   - Scanlines, glitch effects, neon pulse all disabled
   - Buttons have rounded corners (no clip-path)
   - All panels, dialogs, tabs use light theme colors
   - Font changes to Inter/system-ui
4. Switch back to "Cyberpunk Dark"
5. Verify: original appearance fully restored
6. Refresh page: last selected theme persists

- [ ] **Step 4: Commit**

```bash
git add web/theme/light-professional.json web/STL_generate.html
git commit -m "feat(theme): add light professional theme with Inter font"
```

---

## Task 10: Final verification and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Full visual regression check**

With Cyberpunk Dark theme:
1. Left panel (model tree) — node labels, expanders, action buttons
2. Right panel (inspector) — property rows, inputs, section headers
3. Toolbar — all buttons, dropdowns
4. Log panel — log entries with different categories
5. Mesh settings panel — all section headers, inputs
6. Solver settings panel — section headers, inputs
7. Postprocess panel — buttons, inputs
8. Load Project dialog — tabs, file list, buttons
9. File Browser dialog — directory listing, hover, selection
10. Tab switching (Inspector / Mesh Preview / Solver Monitor)

- [ ] **Step 2: Light theme visual check**

Switch to Light Professional and repeat the same 10 checks above. Verify no text is invisible (same color as background) and all elements are readable.

- [ ] **Step 3: Fallback test**

Temporarily rename `theme-loader.js` to `theme-loader.js.bak`. Reload page. Verify:
- No console errors that break functionality
- Page renders with original cyberpunk appearance (CSS fallback values)
- Theme dropdown is empty but page is functional

Rename back to `theme-loader.js`.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(theme): complete theme system with cyberpunk-dark and light-professional themes"
```
