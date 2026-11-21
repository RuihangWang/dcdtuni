/**
 * ThemeManager — switches between theme CSS files.
 *
 * Usage:
 *   <link rel="stylesheet" href="theme/base.css">
 *   <link rel="stylesheet" href="theme/vscode-dark.css" id="theme-css">
 *   <script src="theme/theme-loader.js"></script>
 *   Then call ThemeManager.init() in DOMContentLoaded.
 */
const ThemeManager = {
  _current: null,
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
      console.warn('[ThemeManager] Could not load theme manifest.', e);
      return;
    }
    const saved = localStorage.getItem('stlgen-theme');
    const themeName = (saved && this._manifest.themes.some(t => t.id === saved))
      ? saved
      : this._manifest.default;
    this.loadTheme(themeName);
    this._populateSelector();
  },

  /**
   * Load a theme by switching the CSS file.
   */
  loadTheme(themeName) {
    const link = document.getElementById('theme-css');
    if (link) {
      // Cache-bust so a CSS edit lands without a hard reload. Keep in sync with
      // the ?v= on the <link> tags in STL_generate.html.
      link.href = `theme/${themeName}.css?v=20260826`;
    }
    this._current = themeName;
    localStorage.setItem('stlgen-theme', themeName);
    // Update selector if it exists
    const sel = document.getElementById('theme-selector');
    if (sel) sel.value = themeName;
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
   * Get current theme name.
   */
  getCurrentTheme() {
    return this._current;
  }
};
