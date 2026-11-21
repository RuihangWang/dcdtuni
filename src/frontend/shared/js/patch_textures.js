/**
 * Pictures on patches: return grilles and supply outlets.
 *
 * A patch record may carry `textureDataUrl` (a picture loaded from disk and
 * embedded in the .cfdproj), `textureUrl` (a served asset) or
 * `texturePreset` (one of the presets, served from the texture pool). The
 * geometry builder maps whichever is set over the patch (UVs stretched 0..1
 * across it); the inspectors in both editors offer the presets as a dropdown
 * and the load dialog for a custom picture.
 *
 * The presets are the manifest's `patchPresets` (textures/materials.json,
 * read through js/material_library.js); their ids are what a saved project
 * stores, and each one's URL is `/textures/<file>`. Presets are filtered by
 * the patch's role: a return grille gets grille and diffuser styles, a supply
 * outlet the louvre and the split-unit photo.
 */
(function () {
  'use strict';
  function library() {
    var root = typeof globalThis !== 'undefined' ? globalThis : (typeof window !== 'undefined' ? window : null);
    var lib = root && root.materialLibrary;
    if (!lib && typeof module !== 'undefined' && module.exports && typeof require === 'function') lib = require('./material_library.js');
    if (!lib) throw new Error('patch_textures.js needs js/material_library.js loaded first');
    return lib;
  }
  var _presets = null, _from = null;
  function presets() {
    var lib = library();
    if (!lib.isReady()) return [];
    var m = lib.manifest();
    if (_presets && _from === m) return _presets;
    _from = m;
    _presets = lib.patchPresets().map(function (p) {
      return { id: p.id, roles: p.roles.slice(), url: lib.textureUrl('/textures', p.image.file), labelKey: p.labelKey };
    });
    return _presets;
  }
  function byId(id) {
    var all = presets();
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }
  function forRole(role) {
    return presets().filter(function (p) { return !role || p.roles.indexOf(role) >= 0; });
  }
  function sourceFor(patch) {
    if (!patch) return null;
    if (typeof patch.textureDataUrl === 'string' && patch.textureDataUrl) return patch.textureDataUrl;
    if (typeof patch.textureUrl === 'string' && patch.textureUrl) return patch.textureUrl;
    var pr = patch.texturePreset ? byId(patch.texturePreset) : null;
    return pr ? pr.url : null;
  }
  var api = { byId: byId, forRole: forRole, sourceFor: sourceFor };
  Object.defineProperty(api, 'presets', { get: presets, enumerable: true });
  if (typeof window !== 'undefined') window.patchTexturePresets = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
