"""python -m pytest tools/dctwin_export/test_strip_python.py"""
import re

import pytest

from strip_python import StripError, strip_source

SOURCE = '''import os

# helper for the display
def handle_ov_status(h):
    return 1


def handle_keep(h):
    return 2


def route(path, h):
    if path == "/api/ov-status":
        handle_ov_status(h)
    elif path == "/api/keep":
        handle_keep(h)
    elif path == "/api/ov/x":
        handle_ov_status(h)
    else:
        return None
    if path == "/api/ov/only":
        return handle_ov_status(h)
    return 0


if __name__ == "__main__":
    import release_check
    release_check.start()
'''


def strip(**kw):
    return strip_source(SOURCE, defs=re.compile(r"handle_ov_\w+"), branch_tests=re.compile(r"/api/ov"),
                        statements=re.compile(r"release_check"), **kw)


def test_chain_head_removed_and_next_branch_becomes_if():
    out, report = strip()
    assert 'if path == "/api/keep":' in out and "elif" not in out
    assert "else:\n        return None" in out
    assert "/api/ov" not in out and "handle_ov_status" not in out
    assert "# helper for the display" not in out
    assert report["defs"] == ["handle_ov_status"]
    compile(out, "x", "exec")


def test_block_emptied_by_statement_cuts_takes_its_parent():
    out, _ = strip()
    assert "__main__" not in out and "release_check" not in out


def test_removed_definition_still_referenced_fails():
    with pytest.raises(StripError, match="still referenced"):
        strip_source(SOURCE, defs=re.compile(r"handle_ov_\w+"))


def test_drop_line_pattern_must_match():
    with pytest.raises(StripError, match="matched nothing"):
        strip(drop_lines=[re.compile(r"^no such line")])
