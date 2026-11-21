"""The automation contract's verbs, read from its source: name, signature, whether ``build`` may call it, one line.

``window.__dcdtuni`` is ``{...projectStore, ...automationVerbs}``
(``src/frontend/react/src/automation/contract.ts``). This module reads that file,
``projectStore.ts`` and ``BUILD_VERBS`` (``headless/projectBuilder.ts``) and
returns one row per verb, so the table an agent works from is generated from
the code instead of copied by hand::

    python -m agent.scripted_api.contract_verbs                 # JSON rows
    python -m agent.scripted_api.contract_verbs --markdown      # the tables
    python -m agent.scripted_api.contract_verbs --update .claude/commands/dc-builder.md
    python -m agent.scripted_api.contract_verbs --check  .claude/commands/dc-builder.md

``--update`` rewrites the block between ``<!-- contract-verbs:begin`` and
``<!-- contract-verbs:end -->``; ``--check`` exits 1 when that block is stale.
The parse is static (no Node); ``backend/tests/test_api/test_contract_verbs.py``
checks its verb set against the live contract (headless operation
``contract-verbs``), so a verb this parser misses fails a test rather than
dropping out of the table.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent.scripted_api.identity import REPO

REACT_SRC = REPO / "src" / "frontend" / "react" / "src"
CONTRACT = REACT_SRC / "automation" / "contract.ts"
STORE = REACT_SRC / "projectStore.ts"
BUILDER = REACT_SRC / "headless" / "projectBuilder.ts"
BEGIN = "<!-- contract-verbs:begin"
END = "<!-- contract-verbs:end -->"
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_OPEN = {"(": ")", "[": "]", "{": "}"}


# ── a small TypeScript scanner: strings, comments and bracket matching ───────

def _skip_string(src: str, i: int) -> int:
    """``src[i]`` opens a string or template; return the index after it."""
    quote = src[i]
    i += 1
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if quote == "`" and src.startswith("${", i):
            i = _match(src, i + 1) + 1
            continue
        if ch == quote:
            return i + 1
        i += 1
    return i


def _skip_comment(src: str, i: int) -> int:
    if src.startswith("//", i):
        end = src.find("\n", i)
        return len(src) if end < 0 else end
    end = src.find("*/", i + 2)
    return len(src) if end < 0 else end + 2


def _is_regex_start(src: str, i: int) -> bool:
    """A ``/`` that starts a regex literal (after an operator or opener), not a division."""
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    return j < 0 or src[j] in "(,=:[!&|?{};+-*%<>~^"


def _skip_regex(src: str, i: int) -> int:
    i += 1
    in_class = False
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            i += 1
            while i < len(src) and src[i].isalpha():
                i += 1
            return i
        elif ch == "\n":
            return i
        i += 1
    return i


def _match(src: str, i: int) -> int:
    """``src[i]`` is ``(``, ``[`` or ``{``; return the index of its closing bracket."""
    stack = [_OPEN[src[i]]]
    i += 1
    while i < len(src):
        ch = src[i]
        if ch in "'\"`":
            i = _skip_string(src, i)
            continue
        if src.startswith("//", i) or src.startswith("/*", i):
            i = _skip_comment(src, i)
            continue
        if ch == "/" and _is_regex_start(src, i):
            i = _skip_regex(src, i)
            continue
        if ch in _OPEN:
            stack.append(_OPEN[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError("unbalanced brackets")


def _squash(text: str) -> str:
    """Collapse whitespace; a line break between two members of an object type becomes ``; ``."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    out = lines[0]
    for line in lines[1:]:
        joined = out[-1] in "{([,;|&:=>" or line[0] in "})]|&=."
        out += (" " if joined else "; ") + line
    return re.sub(r"\s+", " ", out).strip()


# ── documentation comments ───────────────────────────────────────────────────

def _comment_before(src: str, pos: int) -> str:
    """The ``/** */`` block, or the run of ``//`` lines, ending just before ``pos``."""
    head = src[:pos].rstrip()
    for prefix in ("export", "async"):  # `export async function` -> look before both words
        if head.endswith(prefix):
            head = head[: -len(prefix)].rstrip()
    if head.endswith("*/"):
        start = head.rfind("/**")
        if start < 0:
            return ""
        lines = [re.sub(r"^\s*\*\s?", "", line) for line in head[start + 3:-2].splitlines()]
        return "\n".join(lines).strip()
    lines: List[str] = []
    for line in reversed(head.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("//") or re.match(r"//\s*──", stripped):
            break
        lines.append(stripped[2:].strip())
    return "\n".join(reversed(lines)).strip()


def _first_sentence(doc: str) -> str:
    """First sentence of the first paragraph, without a leading ``code span —``."""
    if not doc:
        return ""
    para = ""
    for block in re.split(r"\n\s*\n", doc.strip()):
        text = re.sub(r"\s+", " ", block).strip()
        text = re.sub(r"^`[^`]*`\s*(?:[—–-]+|:)\s*", "", text)
        if re.sub(r"`[^`]*`|[\s.:—–-]", "", text):
            para = text
            break
    guarded = para.replace("e.g.", "e\x00g\x00").replace("i.e.", "i\x00e\x00").replace("etc.)", "etc\x00)")
    found = re.match(r"(.+?[.!?])(?=\s|$)", guarded)
    sentence = (found.group(1) if found else guarded).replace("\x00", ".")
    return sentence if len(sentence) <= 220 else sentence[:217].rstrip() + "…"


# ── function signatures ──────────────────────────────────────────────────────

def _after_params(src: str, close: int, arrow: bool = False) -> Tuple[str, int]:
    """The return type after ``)`` at ``close`` (``""`` when none) and where the body (or an arrow's ``=>``) starts."""
    i = close + 1
    while i < len(src) and src[i] in " \t\r\n":
        i += 1
    if i >= len(src) or src[i] != ":":
        return "", i
    j = i + 1
    while j < len(src):
        if arrow and src.startswith("=>", j):
            return _squash(src[i + 1:j]), j
        ch = src[j]
        if ch == "{":
            rest = src[j + 1:src.find("\n", j)] if "\n" in src[j:] else ""
            if not rest.strip():  # the body: `{` closes its line; an object TYPE carries members on the same line
                return _squash(src[i + 1:j]), j
            j = _match(src, j) + 1
            continue
        if ch in "([":
            j = _match(src, j) + 1
            continue
        if ch in "'\"`":
            j = _skip_string(src, j)
            continue
        j += 1
    return _squash(src[i + 1:]), j


def _function(src: str, name: str) -> Optional[Dict[str, str]]:
    """``function name(...)`` or ``const name = (...) =>`` in ``src``: params, return type, doc."""
    found = re.search(rf"(?m)^[ \t]*(?:export\s+)?(?:async\s+)?function\s+{re.escape(name)}\s*(?:<[^>()]*>)?\s*\(",
                      src)
    arrow = False
    if not found:
        arrow = True
        found = re.search(rf"(?m)^[ \t]*(?:export\s+)?const\s+{re.escape(name)}\s*(?::[^=]+)?=\s*(?:async\s*)?\(",
                          src)
    if not found:
        return None
    open_paren = found.end() - 1
    close = _match(src, open_paren)
    returns, _ = _after_params(src, close, arrow=arrow)
    start = found.start() + len(found.group(0)) - len(found.group(0).lstrip())
    return {"params": _squash(src[open_paren + 1:close]).rstrip(","), "returns": returns,
            "doc": _comment_before(src, start)}


def _import_source(src: str, name: str, here: Path) -> Optional[Path]:
    """The module ``name`` is imported from (``import { a, b as name } from './x'``)."""
    for match in re.finditer(r"import\s*(?:type\s*)?\{([^}]*)\}\s*from\s*'([^']+)'", src):
        for part in match.group(1).split(","):
            bits = part.replace("type ", "").split(" as ")
            local = bits[-1].strip()
            if local == name:
                base = (here.parent / match.group(2)).resolve()
                for candidate in (base.with_suffix(".ts"), base.with_suffix(".tsx"), base / "index.ts"):
                    if candidate.is_file():
                        return candidate
    return None


def _resolve(name: str, src: str, path: Path, imported: Optional[str] = None) -> Dict[str, str]:
    """Signature and doc of the function ``name`` refers to in ``path`` (following one import)."""
    local = _function(src, name)
    if local:
        return local
    origin = _import_source(src, name, path)
    if origin is not None:
        found = _function(origin.read_text(encoding="utf-8"), imported or name)
        if found:
            return found
    return {"params": "", "returns": "", "doc": ""}


# ── the contract object ──────────────────────────────────────────────────────

def _object_entries(src: str, marker: str) -> List[Tuple[str, str, str]]:
    """``(leading comments, entry text, section)`` for each top-level entry of the object after ``marker``."""
    start = src.index(marker)
    open_brace = src.index("{", start)
    close = _match(src, open_brace)
    body = src[open_brace + 1:close]
    entries: List[Tuple[str, str, str]] = []
    section, trivia, i, entry_start = "", "", 0, None
    while i < len(body):
        ch = body[i]
        if entry_start is None:
            if ch in " \t\r\n,":
                i += 1
                continue
            if body.startswith("//", i) or body.startswith("/*", i):
                end = _skip_comment(body, i)
                text = body[i:end]
                sect = re.match(r"//\s*──\s*([^─(]+)", text)
                if sect:
                    section, trivia = sect.group(1).strip(), ""
                else:
                    trivia += text + "\n"
                i = end
                continue
            entry_start = i
        if ch in "'\"`":
            i = _skip_string(body, i)
            continue
        if body.startswith("//", i) or body.startswith("/*", i):
            i = _skip_comment(body, i)
            continue
        if ch in _OPEN:
            i = _match(body, i) + 1
            continue
        if ch == ",":
            entries.append((trivia, body[entry_start:i].strip(), section))
            trivia, entry_start = "", None
        i += 1
    if entry_start is not None and body[entry_start:].strip():
        entries.append((trivia, body[entry_start:].strip(), section))
    return entries


def _trivia_doc(trivia: str) -> str:
    """Doc text of the comments directly in front of an object entry."""
    if not trivia.strip():
        return ""
    return _comment_before(trivia.rstrip() + "\n", len(trivia.rstrip()) + 1)


def _arrow_params(value: str) -> Optional[Tuple[str, str, str]]:
    """``(params, returns, body)`` of an arrow function expression, or None."""
    text = value.strip()
    if text.startswith("async"):
        text = text[5:].lstrip()
    if text.startswith("("):
        close = _match(text, 0)
        returns, at = _after_params(text, close, arrow=True)
        if text[at:at + 2] == "=>":
            return _squash(text[1:close]).rstrip(","), returns, text[at + 2:].strip()
        return None
    single = re.match(r"([A-Za-z_$][\w$]*)\s*=>", text)
    if single:
        return single.group(1), "", text[single.end():].strip()
    return None


def contract_rows() -> List[Dict[str, str]]:
    """One row per verb of ``automationVerbs`` (source order), then one per store export (source order)."""
    src = CONTRACT.read_text(encoding="utf-8")
    store_src = STORE.read_text(encoding="utf-8")
    build = set(build_verbs())
    rows: List[Dict[str, str]] = []
    seen = set()
    for trivia, entry, section in _object_entries(src, "const automationVerbs = {"):
        key = _IDENT.match(entry)
        if not key:
            raise ValueError(f"cannot read the contract entry {entry[:60]!r}")
        name = key.group(0)
        value = entry[key.end():].lstrip()
        doc = _trivia_doc(trivia) if "/**" in trivia else ""
        line_doc = "" if doc else _trivia_doc(trivia)
        call = ""
        if not value:  # shorthand `name,`
            info = _resolve(name, src, CONTRACT)
        elif value.startswith(":"):
            value = value[1:].strip()
            if _IDENT.fullmatch(value):  # alias `name: other`
                info = _resolve(value, src, CONTRACT)
            else:
                arrow = _arrow_params(value)
                if arrow is None:
                    raise ValueError(f"cannot read the contract entry {name}: {value[:60]!r}")
                params, returns, body = arrow
                called = re.match(r"([A-Za-z_$][\w$]*)\(", body)
                inner = _resolve(called.group(1), src, CONTRACT) if called else {"doc": "", "returns": ""}
                if called and called.group(1) != name and len(body) <= 50 and body.endswith(")"):
                    call = body
                info = {"params": params, "returns": returns or inner.get("returns", ""),
                        "doc": inner.get("doc", "")}
        else:
            raise ValueError(f"cannot read the contract entry {entry[:60]!r}")
        seen.add(name)
        summary = _first_sentence(doc or info["doc"] or line_doc)
        if call:
            summary = f"{summary} (`{call}`)" if summary else f"`{call}`"
        rows.append({"name": name, "source": "contract", "group": section, "params": info["params"],
                     "returns": info["returns"], "build": name in build, "summary": summary})
    for name in store_exports(store_src):
        if name in seen:  # the verbs object overrides a store export of the same name
            continue
        info = _function(store_src, name) or {"params": "", "returns": "", "doc": ""}
        rows.append({"name": name, "source": "store", "group": "store", "params": info["params"],
                     "returns": info["returns"], "build": name in build,
                     "summary": _first_sentence(info["doc"])})
    return rows


def store_exports(store_src: Optional[str] = None) -> List[str]:
    """Runtime names ``import * as store`` brings into the contract (functions and values, not types)."""
    src = store_src if store_src is not None else STORE.read_text(encoding="utf-8")
    found = [(m.start(), m.group(1)) for m in re.finditer(
        r"(?m)^export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", src)]
    for group in re.finditer(r"(?m)^export\s*\{([^}]*)\}", src):
        found += [(group.start(), part.split(" as ")[-1].strip()) for part in group.group(1).split(",")
                  if part.strip() and not part.strip().startswith("type ")]
    names: List[str] = []
    for _, name in sorted(found):
        if name not in names:
            names.append(name)
    return names


def build_verbs() -> List[str]:
    """``BUILD_VERBS`` in ``projectBuilder.ts``: the verbs a headless ``build`` may call."""
    src = BUILDER.read_text(encoding="utf-8")
    start = src.index("export const BUILD_VERBS = [")
    close = _match(src, src.index("[", start))
    return re.findall(r"'([^']+)'", src[start:close])


def verb_names() -> List[str]:
    return [row["name"] for row in contract_rows()]


# ── markdown ────────────────────────────────────────────────────────────────

def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _signature(row: Dict[str, str]) -> str:
    sig = f"{row['name']}({row['params']})"
    if row["returns"] and row["returns"] != "void":
        sig += f" → {row['returns']}"
    return f"`{_cell(sig)}`"


def markdown(rows: Optional[List[Dict[str, str]]] = None) -> str:
    rows = rows if rows is not None else contract_rows()
    verbs = [r for r in rows if r["source"] == "contract"]
    store = [r for r in rows if r["source"] == "store"]
    out = [f"`window.__dcdtuni` holds {len(rows)} verbs: {len(verbs)} from `automationVerbs` in "
           "`src/frontend/react/src/automation/contract.ts` and the "
           f"{len(store)} exports of `projectStore.ts` it spreads in. **build** marks the "
           f"{sum(1 for r in rows if r['build'])} that `python -m agent.scripted_api build` may call "
           "(`BUILD_VERBS` in `headless/projectBuilder.ts`); the others need the 3D scene, the view, "
           "a server or the undo history, so only the editor page (`/api/browser/exec-js`) runs them.",
           "",
           "| Verb | Group | build | What it does |",
           "|---|---|---|---|"]
    for row in verbs:
        out.append(f"| {_signature(row)} | {row['group']} | {'yes' if row['build'] else ''} | "
                   f"{_cell(row['summary']) or '—'} |")
    out += ["", "Store methods (`projectStore.ts`), reached through the same object:", "",
            "| Verb | build | What it does |", "|---|---|---|"]
    for row in store:
        out.append(f"| {_signature(row)} | {'yes' if row['build'] else ''} | {_cell(row['summary']) or '—'} |")
    return "\n".join(out) + "\n"


def _block(text: str) -> Tuple[int, int]:
    begin = text.find(BEGIN)
    end = text.find(END)
    if begin < 0 or end < begin:
        raise ValueError(f"no {BEGIN} ... {END} block")
    return text.index("\n", begin) + 1, end


def current_block(path: Path | str) -> str:
    text = Path(path).read_text(encoding="utf-8")
    start, end = _block(text)
    return text[start:end]


def update_file(path: Path | str) -> bool:
    """Rewrite the generated block in ``path``; True when it changed."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    start, end = _block(text)
    fresh = markdown()
    if text[start:end] == fresh:
        return False
    path.write_text(text[:start] + fresh + text[end:], encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent.scripted_api.contract_verbs", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--markdown", action="store_true", help="print the verb tables")
    mode.add_argument("--update", metavar="FILE", help="rewrite the generated block in FILE")
    mode.add_argument("--check", metavar="FILE", help="exit 1 when the generated block in FILE is stale")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update:
        changed = update_file(args.update)
        print(f"{args.update}: {'updated' if changed else 'already current'}")
        return 0
    if args.check:
        stale = current_block(args.check) != markdown()
        print(f"{args.check}: {'STALE - run --update' if stale else 'current'}")
        return 1 if stale else 0
    if args.markdown:
        sys.stdout.write(markdown())
        return 0
    print(json.dumps(contract_rows(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
