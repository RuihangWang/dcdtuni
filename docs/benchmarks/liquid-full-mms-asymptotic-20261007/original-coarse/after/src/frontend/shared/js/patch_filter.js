(function () {
  'use strict';

  function buildPredicate(state) {
    const { text, case: caseOn, whole, regex } = state;
    if (!text) return () => true;

    // Regex mode takes precedence: when `regex` is on, the `whole` flag is
    // ignored — the user writes anchors (^...$) themselves if they want a
    // whole-string match.
    if (regex) {
      try {
        const re = new RegExp(text, caseOn ? '' : 'i');
        return (name) => re.test(name);
      } catch (_) {
        return () => false;
      }
    }

    const haystackOf = caseOn ? (s) => s : (s) => s.toLowerCase();
    const needle = caseOn ? text : text.toLowerCase();

    if (whole) {
      return (name) => haystackOf(name) === needle;
    }
    return (name) => haystackOf(name).indexOf(needle) !== -1;
  }

  function isRegexValid(state) {
    if (!state.regex || !state.text) return true;
    try { new RegExp(state.text); return true; } catch (_) { return false; }
  }

  function createFilterBar({ container, onChange }) {
    const bar = document.createElement('div');
    bar.className = 'patch-filter-bar';
    bar.innerHTML = `
      <button type="button" class="btn-toggle" data-mode="case"  title="区分大小写 (Aa)">Aa</button>
      <button type="button" class="btn-toggle" data-mode="whole" title="全字匹配 (ab|)">ab|</button>
      <button type="button" class="btn-toggle" data-mode="regex" title="正则 (.*)">.*</button>
      <input type="text" class="filter-input" placeholder="过滤 patch..." />
      <span class="filter-count"></span>
    `;
    // Defensive: if the caller re-invokes us on the same container without
    // first removing the old bar, strip the leftover so we don't stack.
    const existing = container.querySelector(':scope > .patch-filter-bar');
    if (existing) existing.remove();
    container.insertBefore(bar, container.firstChild);

    const input = bar.querySelector('.filter-input');
    const btns = {
      case:  bar.querySelector('[data-mode="case"]'),
      whole: bar.querySelector('[data-mode="whole"]'),
      regex: bar.querySelector('[data-mode="regex"]'),
    };

    const state = { text: '', case: false, whole: false, regex: false };

    function recompute() {
      input.classList.toggle('invalid', !isRegexValid(state));
      instance.predicate = buildPredicate(state);
      if (typeof onChange === 'function') onChange(instance.predicate);
    }

    input.addEventListener('input', () => {
      state.text = input.value;
      recompute();
    });

    Object.keys(btns).forEach((mode) => {
      btns[mode].addEventListener('click', () => {
        state[mode] = !state[mode];
        btns[mode].classList.toggle('active', state[mode]);
        recompute();
      });
    });

    const instance = {
      predicate: buildPredicate(state),
      getState: () => ({ ...state }),
      setState: (next) => {
        state.text = next.text ?? '';
        state.case = !!next.case;
        state.whole = !!next.whole;
        state.regex = !!next.regex;
        input.value = state.text;
        btns.case.classList.toggle('active', state.case);
        btns.whole.classList.toggle('active', state.whole);
        btns.regex.classList.toggle('active', state.regex);
        recompute();
      },
      clear: () => {
        state.text = '';
        state.case = false;
        state.whole = false;
        state.regex = false;
        input.value = '';
        btns.case.classList.remove('active');
        btns.whole.classList.remove('active');
        btns.regex.classList.remove('active');
        recompute();
      },
      updateCount: (matched, total) => {
        bar.querySelector('.filter-count').textContent = `${matched} / ${total}`;
      },
      element: bar,
    };

    return instance;
  }

  window.createFilterBar = createFilterBar;
})();
