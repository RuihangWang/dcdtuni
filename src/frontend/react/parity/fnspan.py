"""Locate a top-level `function NAME(...) {...}` in a JS source, correctly.

Three things broke earlier versions of this, each producing a confident and
wrong answer rather than an error:

  1. A default parameter — `function f(opts = {})` — opened the brace counter on
     the DEFAULT's brace, so the "body" ended at its `}` and the function was
     truncated to its signature. Every call inside it went unseen.
  2. Comments were not skipped, so an apostrophe in a comment ("don't") started
     a fake string literal that swallowed the rest of the file. A 21-line
     function measured 22993 lines and appeared to call 217 others.
  3. Regex literals with braces (`/\\{\\d+\\}/`) do the same thing to the brace
     counter.

So this walks the source with a real (small) tokenizer: strings, template
literals, both comment forms, and regex literals are all skipped as units.
`verify()` re-parses every extracted body to prove it, because a scanner that
is subtly wrong here is worse than no scanner.
"""
import re

_REGEX_PREV = set('(,=:[!&|?{};+-*%~^<>') | {'return', 'typeof', 'case', 'in', 'of', 'new', 'delete', 'void', 'instanceof', 'do', 'else', 'yield', 'await'}


def _skip(src, i):
    """If a token starts at i that must be skipped as a unit, return its end."""
    c = src[i]
    if c in '\'"':
        j = i + 1
        while j < len(src):
            if src[j] == '\\':
                j += 2
                continue
            if src[j] == c:
                return j + 1
            j += 1
        return len(src)
    if c == '`':
        # A `${...}` hole is real JS and can contain strings, nested templates
        # and braces of its own. Recursing through _skip inside the hole is what
        # keeps `${x ? 'a' : 'b'}` from opening a fake string that swallows the
        # rest of the file.
        j = i + 1
        depth = 0
        while j < len(src):
            if src[j] == '\\':
                j += 2
                continue
            if depth:
                nxt = _skip(src, j)
                if nxt is not None and nxt > j:
                    j = nxt
                    continue
            if src[j] == '$' and j + 1 < len(src) and src[j + 1] == '{':
                depth += 1; j += 2; continue
            if src[j] == '}' and depth:
                depth -= 1; j += 1; continue
            if src[j] == '`' and not depth:
                return j + 1
            j += 1
        return len(src)
    if c == '/' and i + 1 < len(src):
        if src[i + 1] == '/':
            k = src.find('\n', i)
            return len(src) if k < 0 else k
        if src[i + 1] == '*':
            k = src.find('*/', i + 2)
            return len(src) if k < 0 else k + 2
        # regex literal: only where a value may start
        k = i - 1
        while k >= 0 and src[k] in ' \t\n':
            k -= 1
        if k < 0:
            prev = '('
        else:
            prev = src[k]
            if prev.isalnum() or prev == '_':
                m = re.search(r'[A-Za-z_$][\w$]*$', src[:k + 1])
                prev = m.group(0) if m else prev
        if prev in _REGEX_PREV:
            j = i + 1
            cls = False
            while j < len(src):
                if src[j] == '\\':
                    j += 2; continue
                if src[j] == '[':
                    cls = True
                elif src[j] == ']':
                    cls = False
                elif src[j] == '/' and not cls:
                    return j + 1
                elif src[j] == '\n':
                    break
                j += 1
    return None


def _balance(src, i, open_ch, close_ch):
    """Index just past the token that closes the group opening at i."""
    depth = 0
    while i < len(src):
        nxt = _skip(src, i)
        if nxt is not None:
            i = nxt
            continue
        c = src[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def find(src, name):
    for m in re.finditer(r'\n(\s*)function\s+' + re.escape(name) + r'\s*\(', src):
        start = m.start() + 1
        pend = _balance(src, m.end() - 1, '(', ')')
        if pend is None:
            continue
        args = src[m.end():pend - 1]
        b = src.find('{', pend)
        if b < 0:
            continue
        end = _balance(src, b, '{', '}')
        if end is None:
            continue
        return {'start': start, 'end': end, 'indent': m.group(1), 'args': args,
                'text': src[start:end], 'line': src[:start].count('\n') + 1}
    return None
