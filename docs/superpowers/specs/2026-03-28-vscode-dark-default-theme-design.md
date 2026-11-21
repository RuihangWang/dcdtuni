# VSCode Dark Default Theme — Design Spec

**Date**: 2026-03-28
**Branch**: `feat/theme-system`

## Goal

Replace the current default theme (`cyberpunk-dark`) with a new `vscode-dark` theme derived from the sister repo (`~/mycode/dctwin/Stlgenerator`). Delete the `light-professional` theme. Keep `cyberpunk-dark` as an alternate.

## Source of Truth

The sister repo's `STL_generate.html` uses a VS Code–inspired professional dark palette:
- Background: `#1e1e1e` (VS Code default dark)
- Panels: `rgba(37,37,38,0.96)` with `backdrop-filter: blur(6px)`
- Borders: `#3c3c3c`
- Text: `#cccccc`
- Accent: `#4daafc` (sky blue)
- Active: `#094771` (deep blue selection)
- Danger: `#f14c4c`, Success: `#16825d`
- Log colors: One Dark palette (`#98c379`, `#e5c07b`, `#61afef`, `#c678dd`, `#56b6c2`)
- Fonts: `'Segoe UI', system-ui, sans-serif` (body), `monospace` only for log/code
- No visual effects (no scanlines, glitch, neon pulse, clip-path corners)
- Viewport: `radial-gradient(circle at center, #2e2e33 0%, #111112 100%)`

## Changes

### 1. New file: `web/theme/vscode-dark.json`

Full 86-variable theme JSON matching the Stlgenerator palette. Key mappings:

| Variable Group | Source (Stlgenerator) |
|---|---|
| `colors.bg` | `#1e1e1e` |
| `colors.panel` | `#252526` |
| `colors.border` | `#3c3c3c` |
| `colors.text` / `textDim` | `#cccccc` / `#888888` |
| `colors.highlight` / `accent` | `#4daafc` |
| `colors.active` | `#094771` |
| `colors.danger` / `success` | `#f14c4c` / `#16825d` |
| `extendedColors.logCat*` | One Dark: `#98c379`, `#f14c4c`, `#e5c07b`, `#61afef`, `#c678dd` |
| `extendedColors.sectionHeader*` | Same One Dark palette |
| `extendedColors.panelGradient` | `rgba(37,37,38,0.96)` (flat, no gradient) |
| `extendedColors.viewportBg` | `radial-gradient(circle at center, #2e2e33 0%, #111112 100%)` |
| `extendedColors.viewportGlow` | `none` |
| `typography.fontPrimary` | `'Segoe UI', system-ui, -apple-system, sans-serif` |
| `typography.fontHeading` | `'Segoe UI', system-ui, -apple-system, sans-serif` |
| `scrollbar.thumbColor` | `#555555` |
| `scrollbar.trackColor` | `#1e1e1e` |
| `effects.*` | All `false` (= all effects disabled) |

### 2. Update `web/theme/themes.json`

```json
{
  "default": "vscode-dark",
  "themes": [
    { "id": "vscode-dark", "displayName": "🖥️ VS Code Dark" },
    { "id": "cyberpunk-dark", "displayName": "🌙 赛博朋克" }
  ]
}
```

### 3. Delete `web/theme/light-professional.json`

### 4. Update HTML `:root` fallback values

All CSS variable fallbacks in `:root` must match `vscode-dark` values so the page renders correctly before `theme-loader.js` executes.

### 5. Remove Inter font `<link>` tag

The Google Fonts `<link>` for Inter was added for the light theme. With `vscode-dark` using system fonts, it's no longer needed.

### 6. No changes to `theme-loader.js` or `cyberpunk-dark.json`

## Non-Goals

- No changes to 3D viewport materials or Three.js rendering
- No changes to theme loader architecture
- No new themes beyond `vscode-dark`
