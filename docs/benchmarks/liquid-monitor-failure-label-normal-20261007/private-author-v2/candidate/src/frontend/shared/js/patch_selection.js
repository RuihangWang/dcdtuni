(function () {
  'use strict';

  function sliceRange(ordered, a, b) {
    const ia = ordered.indexOf(a);
    const ib = ordered.indexOf(b);
    if (ia < 0 || ib < 0) return null;
    const [lo, hi] = ia <= ib ? [ia, ib] : [ib, ia];
    return ordered.slice(lo, hi + 1);
  }

  function setsEqual(a, b) {
    if (a.size !== b.size) return false;
    for (const v of a) if (!b.has(v)) return false;
    return true;
  }

  function createSelectionModel({ onChange } = {}) {
    let selected = new Set();
    let anchor = null;

    function fire(prev, anchorChanged) {
      if (typeof onChange !== 'function') return;
      if (!anchorChanged && setsEqual(prev, selected)) return;
      onChange(prev, new Set(selected));
    }

    function handleRowClick(name, { shift, ctrl, orderedNames }) {
      const prev = new Set(selected);
      const prevAnchor = anchor;

      if (shift && ctrl) {
        const range = anchor && sliceRange(orderedNames, anchor, name);
        if (!range) {
          selected = new Set([name]);
          anchor = name;
        } else {
          range.forEach((n) => selected.add(n));
        }
      } else if (shift) {
        const range = anchor && sliceRange(orderedNames, anchor, name);
        if (!range) {
          selected = new Set([name]);
          anchor = name;
        } else {
          selected = new Set(range);
        }
      } else if (ctrl) {
        if (selected.has(name)) {
          selected.delete(name);
        } else {
          selected.add(name);
          anchor = name;
        }
      } else {
        selected = new Set([name]);
        anchor = name;
      }

      fire(prev, prevAnchor !== anchor);
    }

    function handleCheckboxClick(name, { shift, ctrl, nextVisible }) {
      const hasModifier = !!(shift || ctrl);
      if (hasModifier && selected.size > 0) {
        const union = new Set(selected);
        union.add(name);
        return { targets: [...union], visible: nextVisible };
      }
      return { targets: [name], visible: nextVisible };
    }

    function setSelection(names) {
      const prev = new Set(selected);
      const prevAnchor = anchor;
      selected = new Set(names);
      anchor = names.length > 0 ? names[names.length - 1] : null;
      fire(prev, prevAnchor !== anchor);
    }

    function clear() {
      const prev = new Set(selected);
      const prevAnchor = anchor;
      if (prev.size === 0 && prevAnchor === null) return;
      selected = new Set();
      anchor = null;
      fire(prev, prevAnchor !== null);
    }

    return {
      handleRowClick,
      handleCheckboxClick,
      setSelection,
      clear,
      has: (name) => selected.has(name),
      getAll: () => new Set(selected),
      getLastAnchor: () => anchor,
    };
  }

  window.createSelectionModel = createSelectionModel;
})();
