// Helpers installed into the page once, then called by name from the driver.
window.__V = (function () {
  function pathOf(el) {
    if (el.id) return '#' + el.id;
    var parts = [], n = el;
    while (n && n.nodeType === 1 && n !== document.body) {
      var tag = n.tagName.toLowerCase();
      var cls = (n.className && typeof n.className === 'string')
        ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      var sibs = n.parentNode ? Array.prototype.filter.call(n.parentNode.children,
        function (c) { return c.tagName === n.tagName; }) : [n];
      var idx = sibs.indexOf(n);
      parts.unshift(tag + cls + (sibs.length > 1 ? '[' + idx + ']' : ''));
      if (n.id) { parts[0] = '#' + n.id; break; }
      n = n.parentNode;
    }
    return parts.join('>');
  }
  function labelOf(el) {
    var t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    if (t) return t;
    if (el.getAttribute && el.getAttribute('title')) return '[t]' + el.getAttribute('title').slice(0, 28);
    var row = el.closest && el.closest('.prop-row, .node-label, .tool-inline');
    var lab = row && row.querySelector('label');
    if (lab && lab !== el) return '[r]' + lab.textContent.trim().slice(0, 28);
    return '(' + (el.type || el.tagName.toLowerCase()) + ')';
  }
  function regionOf(el) {
    var ids = ['left-panel', 'right-panel', 'toolbar', 'log-panel', 'center-view'];
    for (var i = 0; i < ids.length; i++) {
      var h = document.getElementById(ids[i]);
      if (h && h.contains(el)) return ids[i];
    }
    return el.id && el.id.indexOf('collapse') >= 0 ? 'collapse' : 'other';
  }
  var SEL = 'button, input, select, textarea, .node-label, .badge.clickable, .collapse-header';
  function all() {
    var seen = [], out = [];
    document.querySelectorAll(SEL).forEach(function (el) {
      if (seen.indexOf(el) >= 0) return;
      seen.push(el);
      var r = el.getBoundingClientRect();
      out.push({
        path: pathOf(el), tag: el.tagName.toLowerCase(), type: el.type || '',
        region: regionOf(el), label: labelOf(el), disabled: !!el.disabled,
        visible: r.width > 0 && r.height > 0,
        value: (el.type === 'checkbox' || el.type === 'radio') ? String(el.checked)
             : (el.value !== undefined ? String(el.value).slice(0, 40) : ''),
        options: el.tagName === 'SELECT'
          ? Array.prototype.map.call(el.options, function (o) { return o.value; }) : undefined
      });
    });
    return out;
  }
  function find(p) {
    // Only take the id shortcut when the path IS an id. A structural path also
    // STARTS with '#' (its nearest id-bearing ancestor), and getElementById on
    // the whole string silently returns null — which read as "control missing"
    // for every control in the sweep.
    if (p.charAt(0) === '#' && p.indexOf('>') < 0) return document.getElementById(p.slice(1));
    var els = document.querySelectorAll(SEL);
    for (var i = 0; i < els.length; i++) if (pathOf(els[i]) === p) return els[i];
    return null;
  }
  // A cheap fingerprint of everything an action could plausibly change.
  function snap() {
    var s = window.__dcdtuni;
    var p = s && s.getProject && s.getProject();
    var vals = [];
    document.querySelectorAll('input, select').forEach(function (el) {
      vals.push((el.id || '') + '=' + ((el.type === 'checkbox' || el.type === 'radio') ? el.checked : el.value));
    });
    return {
      project: p ? JSON.stringify(p).length : 0,
      projectHash: p ? JSON.stringify(p).split('').reduce(function (a, c) { return ((a << 5) - a + c.charCodeAt(0)) | 0; }, 0) : 0,
      undo: s && s.undoDepth ? s.undoDepth() : -1,
      dirty: s && s.isDirty ? s.isDirty() : null,
      logTop: (function () { var l = document.querySelector('#log-content .log-entry'); return l ? l.textContent.trim().slice(0, 70) : ''; })(),
      nControls: document.querySelectorAll(SEL).length,
      inputs: vals.join('|').length,
      inputsHash: vals.join('|').split('').reduce(function (a, c) { return ((a << 5) - a + c.charCodeAt(0)) | 0; }, 0),
      activeTab: (function () { var b = document.querySelector('.left-tab-btn.active'); return b ? b.textContent.trim() : ''; })(),
      canvasPresent: !!document.querySelector('#center-view canvas'),
      // Everything below exists because the sweep was calling WORKING controls
      // dead. A toolbar toggle changes a class, not a value; a scene transform
      // moves the wrapper group, not the project; first-person changes neither.
      // Without these the sweep's verdict on a whole class of controls was
      // "NO_EFFECT", which reads as a defect and is not one.
      //
      // Toggle state: every button that carries an on/off class, by id.
      toggles: (function () {
        var out = [];
        document.querySelectorAll('button').forEach(function (b) {
          var on = b.classList.contains('active') || b.classList.contains('primary');
          out.push((b.id || b.textContent.trim().slice(0, 10)) + (on ? '1' : '0'));
        });
        return out.join('|').split('').reduce(function (a, c) { return ((a << 5) - a + c.charCodeAt(0)) | 0; }, 0);
      })(),
      // The wrapper group the toolbar turns when NOTHING is selected. It is a
      // view transform, so no project field and no camera move records it.
      wrapper: (function () {
        try {
          var g = window.__three && window.__three.globalGroup;
          var w = g && g.parent;
          if (!w) return '';
          return w.rotation.x.toFixed(4) + ',' + w.rotation.y.toFixed(4) + ',' + w.rotation.z.toFixed(4)
               + '|' + w.scale.x.toFixed(3) + ',' + w.scale.y.toFixed(3) + ',' + w.scale.z.toFixed(3);
        } catch (e) { return 'ERR'; }
      })(),
      // First-person is a mode, not a value: it swaps the controls and hides
      // the cursor, and touches nothing the rest of this snapshot reads.
      fps: (function () {
        try {
          // `window.fpsMode`, the global the shared first-person module
          // installs (js/first_person_mode.js) — not a field of __three.
          var f = window.fpsMode;
          return f && f.isActive ? !!f.isActive() : null;
        } catch (e) { return 'ERR'; }
      })(),
      // Button TEXT, because a toggle often announces itself only by relabelling
      // (折叠工具栏 <-> 展开工具栏, 启用旋转 <-> 停止旋转). The `toggles` hash keys
      // id'd buttons by id, so it cannot see those.
      btnLabels: (function () {
        var out = [];
        document.querySelectorAll('button').forEach(function (b) { out.push(b.textContent.trim().slice(0, 14)); });
        return out.join('|').split('').reduce(function (a, c) { return ((a << 5) - a + c.charCodeAt(0)) | 0; }, 0);
      })(),
      // Status and error TEXT. A great many controls answer only here: a
      // checkMesh verdict, an auto-detect "no adjustment needed", an AI panel
      // saying the model path is unset, a post-processing init error. None of
      // it is an input value, so without this the sweep called all of them dead.
      statusText: (function () {
        var out = [];
        document.querySelectorAll('.prop-hint, .status, .error, .empty, [id$="-status"], [id$="-log"], [id$="-verdict"]')
          .forEach(function (e) { out.push(e.textContent.trim().slice(0, 40)); });
        return out.join('|').split('').reduce(function (a, c) { return ((a << 5) - a + c.charCodeAt(0)) | 0; }, 0);
      })(),
      bodyClass: document.body.className,
      selected: document.querySelectorAll('.node-label.selected').length,
      modals: document.querySelectorAll('.modal-overlay, .modal, [id$="-overlay"], [id$="-dialog"]').length,
      // The VIEWPORT, so a control whose only effect is on screen is not
      // reported as dead. Grid previews, camera moves, streamlines, slices and
      // the volume all change here and nowhere else — before this the sweep
      // called every one of them NO_EFFECT, which reads as "broken control".
      scene: (function () {
        var out = { objects: 0, meshes: 0, cam: '', target: '' };
        try {
          var h = window.__viewportHandle;
          if (h && h.camera) {
            var c = h.camera.position;
            out.cam = c.x.toFixed(3) + ',' + c.y.toFixed(3) + ',' + c.z.toFixed(3);
            var t = h.controls && h.controls.target;
            if (t) out.target = t.x.toFixed(3) + ',' + t.y.toFixed(3) + ',' + t.z.toFixed(3);
            var root = h.camera.parent;
            while (root && root.parent) root = root.parent;
            if (root) root.traverse(function (o) {
              out.objects++;
              // VISIBLE only: a patch-visibility toggle changes no object
              // count, so counting everything would call "全选"/"全不选" dead.
              if ((o.isMesh || o.isLineSegments || o.isLine || o.isPoints) && o.visible) out.meshes++;
            });
          }
        } catch (e) { out.err = String(e).slice(0, 40); }
        return out.objects + '/' + out.meshes + '@' + out.cam + '>' + out.target;
      })(),
      err: (function () { var e = document.querySelector('.error'); return e ? e.textContent.trim().slice(0, 80) : ''; })()
    };
  }
  function act(p, mode, arg) {
    var el = find(p);
    if (!el) return 'NO_ELEMENT';
    if (el.disabled) return 'DISABLED';
    var setter;
    if (mode === 'click') { el.click(); return 'clicked'; }
    if (mode === 'check') { el.click(); return 'toggled->' + el.checked; }
    if (mode === 'select') {
      setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
      setter.call(el, arg); el.dispatchEvent(new Event('change', { bubbles: true }));
      return 'selected->' + el.value;
    }
    if (mode === 'text') {
      // Do NOT use el.focus()/el.blur() here. Headless Chromium reports
      // document.hasFocus() === false, and in that state focus()/blur() move
      // activeElement but dispatch NO EVENTS AT ALL — so React's onBlur never
      // fires and a commit-on-blur field never writes. The failure is silent
      // and symmetric: both apps read "unchanged", which the sweep then scores
      // as agreement. Dispatch the focus events explicitly instead.
      //
      // All four are needed: React maps onFocus/onBlur to focusin/focusout,
      // while the legacy editor's inline onchange="" needs a `change` event.
      // Sending only `input` commits in neither app.
      setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      el.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      setter.call(el, arg);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
      return 'typed->' + el.value;
    }
    if (mode === 'range') {
      setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(el, arg);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return 'ranged->' + el.value;
    }
    return 'UNKNOWN_MODE';
  }
  return { all: all, find: find, snap: snap, act: act, pathOf: pathOf };
})();
'installed'
