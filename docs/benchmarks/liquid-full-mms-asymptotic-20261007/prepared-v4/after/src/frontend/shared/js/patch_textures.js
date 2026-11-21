/**
 * Pictures on patches: return grilles and supply outlets.
 *
 * A patch record may carry `textureDataUrl` (a picture loaded from disk and
 * embedded in the .cfdproj), `textureUrl` (a served asset) or
 * `texturePreset` (one of the presets below, served from /textures/grilles).
 * The geometry builder maps whichever is set over the patch (UVs stretched
 * 0..1 across it); the inspectors in both editors offer the presets as a
 * dropdown and the load dialog for a custom picture.
 *
 * Presets are filtered by the patch's role: a return grille gets grille and
 * diffuser styles, a supply outlet the louvre and the split-unit photo.
 */
(function () {
  'use strict';
  var PRESETS = [
    { id: 'louvre_grille',     roles: ['return', 'supply'], url: '/textures/grilles/louvre_grille.jpg',        labelKey: 'texpreset.louvre' },
    { id: 'eggcrate_grille',   roles: ['return'],           url: '/textures/grilles/eggcrate_grille.jpg',      labelKey: 'texpreset.eggcrate' },
    { id: 'four_way_diffuser', roles: ['return', 'supply'], url: '/textures/grilles/four_way_diffuser.jpg',    labelKey: 'texpreset.diffuser4' },
    { id: 'split_ac_outlet',   roles: ['supply'],           url: '/textures/grilles/split_ac_outlet_photo.jpg', labelKey: 'texpreset.split_outlet' },
    { id: 'split_ac_unit',     roles: ['supply', 'return'], url: '/textures/grilles/split_ac_unit_photo.jpg',   labelKey: 'texpreset.split_unit' },
  ];
  function byId(id) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].id === id) return PRESETS[i];
    return null;
  }
  function forRole(role) {
    return PRESETS.filter(function (p) { return !role || p.roles.indexOf(role) >= 0; });
  }
  function sourceFor(patch) {
    if (!patch) return null;
    if (typeof patch.textureDataUrl === 'string' && patch.textureDataUrl) return patch.textureDataUrl;
    if (typeof patch.textureUrl === 'string' && patch.textureUrl) return patch.textureUrl;
    var pr = patch.texturePreset ? byId(patch.texturePreset) : null;
    return pr ? pr.url : null;
  }
  var api = { presets: PRESETS, byId: byId, forRole: forRole, sourceFor: sourceFor };
  if (typeof window !== 'undefined') window.patchTexturePresets = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
