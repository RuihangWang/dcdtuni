(function () {
  'use strict';
  const EXCLUDE = ['id', 'name', 'anchor', 'transform', 'moveTool', 'rotateTool', 'enabled', 'showAnchorMarker', 'fromModel'];

  function extractTemplateFromModel(model, label) {
    const tpl = JSON.parse(JSON.stringify(model));
    EXCLUDE.forEach(k => { delete tpl[k]; });
    tpl.label = label || model.meta.name || (model.kind + ' template');
    return tpl;
  }

  function instantiateTemplate(tpl, templateId, nextIdFn) {
    const inst = JSON.parse(JSON.stringify(tpl));
    delete inst.label;
    inst.id = nextIdFn();
    inst.name = tpl.label || (tpl.kind + '_instance');
    inst.fromModel = templateId || null;
    inst.enabled = true;
    inst.anchor = { x: 0, y: 0, z: 0 };
    return inst;
  }

  if (typeof window !== 'undefined') {
    window.extractTemplateFromModel = extractTemplateFromModel;
    window.instantiateTemplate = instantiateTemplate;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { extractTemplateFromModel, instantiateTemplate };
  }
})();
