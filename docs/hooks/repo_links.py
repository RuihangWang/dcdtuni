"""MkDocs hook: send links that leave the built site to the repository on GitHub.

The Markdown in this repository links to source files (``../../src/...``) and
to directories the site deliberately leaves out (``benchmarks/`` receipts,
``archive/``). Those links work when the Markdown is read in the repository,
but in the built site they would be dead. This hook rewrites them to
``<repo_url>/blob/main/<path>`` (``tree`` for directories) at build time, so
the Markdown stays repository-relative and ``mkdocs build --strict`` reports
only links that are really broken.

Links to pages that are part of the site, external URLs, anchors and links to
paths that do not exist are left alone; a missing target is still reported by
MkDocs. Fenced code blocks are not touched.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

_LINK = re.compile(r'(?<!!)\[([^\]\n]*)\]\(([^)\s]+)((?:\s+"[^"]*")?)\)')
_FENCE = re.compile(r'^(\s*)(```|~~~)')
_SCHEME = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')


def _in_site(files, src_path: str) -> bool:
    found = files.get_file_from_path(src_path)
    if found is None:
        return False
    inclusion = getattr(found, "inclusion", None)
    return inclusion is None or inclusion.is_included()


def on_page_markdown(markdown, page, config, files, **kwargs):
    repo = (config.get("repo_url") or "").rstrip("/")
    if not repo:
        return markdown
    docs_dir = Path(config["docs_dir"]).resolve()
    repo_root = docs_dir.parent
    page_dir = posixpath.dirname(page.file.src_uri)

    def rewrite(match: re.Match) -> str:
        text, target, title = match.group(1), match.group(2), match.group(3)
        if target.startswith(("#", "/")) or _SCHEME.match(target):
            return match.group(0)
        path, _, fragment = target.partition("#")
        if not path:
            return match.group(0)
        rel = posixpath.normpath(posixpath.join(page_dir, path))
        if not rel.startswith("../") and _in_site(files, rel):
            return match.group(0)
        absolute = (docs_dir / rel).resolve()
        try:
            repo_rel = absolute.relative_to(repo_root).as_posix()
        except ValueError:
            return match.group(0)
        if not absolute.exists():
            return match.group(0)
        kind = "tree" if absolute.is_dir() else "blob"
        url = f"{repo}/{kind}/main/{repo_rel}" + (f"#{fragment}" if fragment else "")
        return f"[{text}]({url}{title})"

    out, fence = [], None
    for line in markdown.split("\n"):
        opener = _FENCE.match(line)
        if fence is None and opener:
            fence = opener.group(2)
            out.append(line)
            continue
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            out.append(line)
            continue
        out.append(_LINK.sub(rewrite, line))
    return "\n".join(out)
