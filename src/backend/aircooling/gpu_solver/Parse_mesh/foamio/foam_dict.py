"""Reader and writer for OpenFOAM dictionary files (ASCII and binary, optionally gzipped).

The parser works on bytes so that binary list payloads (``N(<raw bytes>)``) can be read in place.
Values are represented as:

* a single token -> ``int``, ``float``, ``str`` (word) or :class:`FoamString` (quoted string);
* several tokens before ``;`` -> ``tuple`` of values, e.g. ``value uniform (0 0 0);`` -> ``("uniform", array)``;
* ``( ... )`` -> numpy array when all items are numbers or equal-length number tuples, otherwise ``list``;
* ``[ ... ]`` -> :class:`DimensionSet`;
* ``{ ... }`` -> :class:`FoamDict`.

Supported directives: ``#include``, ``#includeIfPresent``, ``#remove``; ``#inputMode`` and ``#includeEtc`` are
ignored. ``$name`` / ``$:a.b`` / ``$a/b`` macro references are expanded on read, using OpenFOAM scoping.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np

__all__ = [
    "FoamDict",
    "FoamString",
    "DimensionSet",
    "FoamHeader",
    "read_foam_file",
    "parse_foam_bytes",
    "write_foam_file",
    "format_value",
]


class FoamString(str):
    """A quoted string token; written back with double quotes."""


class DimensionSet(list):
    """``[kg m s K mol A cd]`` dimension exponents."""


class FoamDict(dict):
    """Ordered dictionary with OpenFOAM regex-key lookup (exact match first, then regex keys, last first)."""

    def lookup(self, key: str, default: Any = KeyError) -> Any:
        if key in self:
            return self[key]
        for k in reversed(list(self.keys())):
            if isinstance(k, FoamString) or (isinstance(k, str) and k.startswith('"')):
                pat = k.strip('"')
                try:
                    if re.fullmatch(pat, key):
                        return self[k]
                except re.error:
                    continue
        if default is KeyError:
            raise KeyError(key)
        return default

    def subdict(self, *keys: str) -> "FoamDict":
        d: Any = self
        for k in keys:
            d = d.lookup(k) if isinstance(d, FoamDict) else d[k]
        if not isinstance(d, FoamDict):
            raise TypeError(f"{'/'.join(keys)} is not a dictionary")
        return d


@dataclass
class FoamHeader:
    version: str = "2.0"
    format: str = "ascii"
    cls: str = "dictionary"
    location: str | None = None
    object: str = ""
    arch: str = "LSB;label=32;scalar=64"
    extra: dict = field(default_factory=dict)

    @property
    def label_dtype(self) -> np.dtype:
        m = re.search(r"label=(\d+)", self.arch or "")
        return np.dtype("<i8") if (m and m.group(1) == "64") else np.dtype("<i4")

    @property
    def scalar_dtype(self) -> np.dtype:
        m = re.search(r"scalar=(\d+)", self.arch or "")
        return np.dtype("<f4") if (m and m.group(1) == "32") else np.dtype("<f8")


# Number of scalar components for the OpenFOAM primitive types that appear as List<T>.
_NCOMP = {
    "scalar": 1,
    "label": 1,
    "bool": 1,
    "vector": 3,
    "symmTensor": 6,
    "tensor": 9,
    "sphericalTensor": 1,
}

_WS = b" \t\r\n\f\v"
_PUNCT = b";{}()[]"
_NUM_RE = re.compile(rb"[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|inf|nan)\Z", re.IGNORECASE)
_INT_RE = re.compile(rb"[+-]?\d+\Z")


def _to_number(tok: bytes):
    if _INT_RE.match(tok):
        return int(tok)
    if _NUM_RE.match(tok):
        return float(tok)
    return None


class _Parser:
    def __init__(self, buf: bytes, path: Path | None, header: FoamHeader | None):
        self.buf = buf
        self.n = len(buf)
        self.pos = 0
        self.path = path
        self.header = header or FoamHeader()
        self.binary = self.header.format == "binary"

    # ------------------------------------------------------------------ lexing
    def skip_ws(self) -> None:
        buf, n = self.buf, self.n
        while self.pos < n:
            c = buf[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == 0x2F and self.pos + 1 < n and buf[self.pos + 1] == 0x2F:  # //
                j = buf.find(b"\n", self.pos)
                self.pos = n if j < 0 else j + 1
            elif c == 0x2F and self.pos + 1 < n and buf[self.pos + 1] == 0x2A:  # /*
                j = buf.find(b"*/", self.pos + 2)
                self.pos = n if j < 0 else j + 2
            else:
                break

    def peek(self) -> int | None:
        self.skip_ws()
        return self.buf[self.pos] if self.pos < self.n else None

    def read_word(self) -> bytes:
        """A raw token: quoted string, punctuation, or a run of non-space non-punctuation bytes."""
        self.skip_ws()
        buf = self.buf
        if self.pos >= self.n:
            raise EOFError
        c = buf[self.pos]
        if c == 0x22:  # "
            j = self.pos + 1
            while True:
                j = buf.find(b'"', j)
                if j < 0:
                    raise ValueError("unterminated string")
                if buf[j - 1] != 0x5C:
                    break
                j += 1
            tok = buf[self.pos : j + 1]
            self.pos = j + 1
            return tok
        if c in _PUNCT:
            self.pos += 1
            return bytes([c])
        start = self.pos
        depth = 0
        while self.pos < self.n:
            c = buf[self.pos]
            if c in _WS:
                break
            if c == 0x28:  # '(' inside a word: div(phi,U) continues, a count such as 4( stops
                if buf[start : self.pos].isdigit():
                    break
                depth += 1
            elif c == 0x29:
                if depth == 0:
                    break
                depth -= 1
            elif c in b";{}[]" and depth == 0:
                break
            elif c == 0x2F and self.pos + 1 < self.n and buf[self.pos + 1] in (0x2F, 0x2A) and depth == 0:
                break
            self.pos += 1
        return buf[start : self.pos]

    # ----------------------------------------------------------------- parsing
    def parse_dict_body(self, scope: list[FoamDict], close: bytes | None) -> FoamDict:
        d = FoamDict()
        scope = scope + [d]
        while True:
            c = self.peek()
            if c is None:
                if close is not None:
                    raise ValueError("unexpected end of file inside a dictionary")
                return d
            if close is not None and bytes([c]) == close:
                self.pos += 1
                return d
            key_tok = self.read_word()
            if key_tok == b";":
                continue
            if key_tok.startswith(b"#"):
                self.directive(key_tok.decode(), d, scope)
                continue
            if key_tok.startswith(b"$") and self.peek() == 0x3B:  # bare "$dict;" merges a dictionary
                self.read_word()
                ref = self.expand(key_tok.decode(), scope)
                if isinstance(ref, FoamDict):
                    d.update(_deepcopy(ref))
                continue
            key = self.token_value(key_tok, scope)
            if not isinstance(key, str):
                key = str(key)
            if self.peek() == 0x7B:  # {
                self.pos += 1
                sub = self.parse_dict_body(scope, b"}")
                if key in d and isinstance(d[key], FoamDict):
                    # Later definitions merge into earlier ones (OpenFOAM default inputMode).
                    d[key].update(sub)
                else:
                    d[key] = sub
                continue
            vals = []
            while True:
                c = self.peek()
                if c is None:
                    break
                if c == 0x3B:  # ;
                    self.pos += 1
                    break
                if c == 0x7D and close == b"}":
                    break
                vals.append(self.parse_value(scope, vals))
            d[key] = vals[0] if len(vals) == 1 else tuple(vals)

    def directive(self, name: str, d: FoamDict, scope: list[FoamDict]) -> None:
        if name in ("#include", "#includeIfPresent", "#sinclude", "#includeFunc", "#includeEtc"):
            arg = self.read_word().decode().strip('"')
            if name in ("#includeFunc", "#includeEtc"):
                return
            if self.path is None:
                return
            inc = Path(_expand_env(arg))
            if not inc.is_absolute():
                inc = self.path.parent / inc
            if not inc.exists():
                if name == "#include":
                    raise FileNotFoundError(inc)
                return
            hdr, sub = _read(inc, scope)
            d.update(sub)
        elif name == "#remove":
            arg = self.parse_value(scope, [])
            keys = arg if isinstance(arg, (list, tuple, np.ndarray)) else [arg]
            for k in keys:
                d.pop(str(k), None)
        elif name in ("#inputMode",):
            self.read_word()
        elif name in ("#calc", "#codeStream", "#eval"):
            raise NotImplementedError(f"OpenFOAM directive {name} is not supported")
        # unknown directives are ignored

    def expand(self, ref: str, scope: list[FoamDict]) -> Any:
        name = ref[1:].strip("{}")
        if name.startswith(":"):
            parts = re.split(r"[.:/]", name[1:])
            node: Any = scope[0]
        else:
            parts = re.split(r"[./]", name)
            node = None
            for s in reversed(scope):
                if parts[0] in s:
                    node = s
                    break
            if node is None:
                env = os.environ.get(parts[0])
                if env is not None and len(parts) == 1:
                    return env
                raise KeyError(f"cannot expand {ref}")
        for p in parts:
            if p == "":
                continue
            node = node[p]
        return _deepcopy(node)

    def token_value(self, tok: bytes, scope: list[FoamDict]):
        if tok.startswith(b'"'):
            return FoamString(tok[1:-1].decode())
        num = _to_number(tok)
        if num is not None:
            return num
        s = tok.decode()
        if s.startswith("$") and len(s) > 1:
            return self.expand(s, scope)
        return s

    def parse_value(self, scope: list[FoamDict], previous: list):
        c = self.peek()
        if c == 0x28:  # (
            self.pos += 1
            return self.parse_list(scope, None, None)
        if c == 0x5B:  # [
            self.pos += 1
            vals = []
            while self.peek() != 0x5D:
                vals.append(self.token_value(self.read_word(), scope))
            self.pos += 1
            return DimensionSet(vals)
        if c == 0x7B:
            self.pos += 1
            return self.parse_dict_body(scope, b"}")
        tok = self.read_word()
        val = self.token_value(tok, scope)
        # "List<vector> N ( ... )" or "N ( ... )" (also binary payloads).
        if isinstance(val, int) and self.peek() == 0x28:
            elem = None
            if previous and isinstance(previous[-1], str) and previous[-1].startswith("List<"):
                elem = previous[-1][5:-1]
            self.pos += 1
            return self.parse_list(scope, val, elem)
        if isinstance(val, int) and self.peek() == 0x7B:  # N{value} uniform list
            self.pos += 1
            v = self.parse_value(scope, [])
            assert self.read_word() == b"}"
            return np.full(val, v) if np.isscalar(v) else np.tile(np.asarray(v), (val, 1))
        return val

    def parse_list(self, scope, count: int | None, elem: str | None):
        """Parse after the opening '('. ``count``/``elem`` enable the fast and binary paths."""
        if count is not None and elem in _NCOMP:
            ncomp = _NCOMP[elem]
            if self.binary:
                dt = (self.header.label_dtype if elem == "label" else
                      np.dtype("u1") if elem == "bool" else self.header.scalar_dtype)
                nbytes = count * ncomp * dt.itemsize
                arr = np.frombuffer(self.buf, dtype=dt, count=count * ncomp, offset=self.pos).copy()
                self.pos += nbytes
                if self.read_word() != b")":
                    raise ValueError("binary list not terminated by ')'")
                return arr.reshape(count, ncomp) if ncomp > 1 else arr
            return self.fast_ascii_list(count, ncomp, elem in ("label", "bool"))
        # An untyped counted list in a binary-format file (e.g. inGroups 1(wall) in polyMesh/boundary) is
        # written as ASCII tokens: OpenFOAM writes raw bytes only for lists of contiguous primitive types.
        if count is not None and count > 64 and self._looks_numeric():
            return self.fast_ascii_list(count, None, False)
        items = []
        while True:
            c = self.peek()
            if c is None:
                raise ValueError("unterminated list")
            if c == 0x29:
                self.pos += 1
                break
            items.append(self.parse_value(scope, items))
        return _maybe_array(items)

    def _looks_numeric(self) -> bool:
        self.skip_ws()
        c = self.buf[self.pos : self.pos + 1]
        return c in (b"(",) or (c and (c.isdigit() or c in b"+-."))

    def fast_ascii_list(self, count: int, ncomp: int | None, integer: bool):
        """Read ``count`` numbers or ``count`` parenthesised tuples followed by ')'."""
        self.skip_ws()
        start = self.pos
        nested = self.buf[start : start + 1] == b"("
        view = np.frombuffer(self.buf, dtype=np.uint8, offset=start)
        closes = np.flatnonzero(view == 0x29)
        k = count if nested else 0  # the (count+1)-th ')' closes the list when items are tuples
        if len(closes) <= k:
            raise ValueError("unterminated numeric list")
        end = start + int(closes[k])
        body = self.buf[start:end]
        if nested:
            body = body.replace(b"(", b" ").replace(b")", b" ")
        arr = np.array(body.split(), dtype=np.int64 if integer else np.float64)
        self.pos = end + 1
        if count == 0:
            return arr.reshape(0, ncomp or 1) if nested else arr
        width = arr.size // count
        if width * count != arr.size:
            raise ValueError("numeric list length mismatch")
        return arr.reshape(count, width) if (nested or width > 1) else arr


def _maybe_array(items: list):
    if items and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in items):
        return np.asarray(items)
    if items and all(isinstance(x, np.ndarray) and x.ndim == 1 for x in items):
        if len({x.shape for x in items}) == 1:
            return np.stack(items)
    return items


def _deepcopy(v):
    if isinstance(v, FoamDict):
        return FoamDict((k, _deepcopy(x)) for k, x in v.items())
    if isinstance(v, np.ndarray):
        return v.copy()
    if isinstance(v, list):
        return type(v)(_deepcopy(x) for x in v)
    return v


def _expand_env(s: str) -> str:
    return os.path.expandvars(s.replace("<case>", os.environ.get("FOAM_CASE", ".")))


def _load_bytes(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return f.read()
    if not path.exists() and path.with_name(path.name + ".gz").exists():
        with gzip.open(path.with_name(path.name + ".gz"), "rb") as f:
            return f.read()
    return path.read_bytes()


def _split_header(buf: bytes) -> tuple[FoamHeader | None, int]:
    m = re.search(rb"FoamFile\s*\{", buf[:4096])
    if not m:
        return None, 0
    # The header may hold a nested dictionary (meta { names ...; } in v2412 zone files): match braces.
    depth, end = 1, m.end()
    while depth:
        j = buf.find(b"}", end)
        o = buf.find(b"{", end, j if j >= 0 else None)
        if j < 0:
            raise ValueError("unterminated FoamFile header")
        if 0 <= o < j:
            depth, end = depth + 1, o + 1
        else:
            depth, end = depth - 1, j + 1
    end -= 1
    p = _Parser(buf[m.end() : end], None, None)
    hd = p.parse_dict_body([], None)
    h = FoamHeader(
        version=str(hd.get("version", "2.0")),
        format=str(hd.get("format", "ascii")),
        cls=str(hd.get("class", "dictionary")),
        location=hd.get("location"),
        object=str(hd.get("object", "")),
        arch=str(hd.get("arch", "LSB;label=32;scalar=64")),
        extra={k: v for k, v in hd.items() if k not in ("version", "format", "class", "location", "object", "arch")},
    )
    return h, end + 1


def _read(path: Path, outer_scope: list[FoamDict] | None = None):
    buf = _load_bytes(path)
    header, off = _split_header(buf)
    p = _Parser(buf, path, header)
    p.pos = off
    d = p.parse_dict_body(outer_scope or [], None)
    return header, d


def parse_foam_bytes(buf: bytes, path: Path | None = None) -> tuple[FoamHeader | None, FoamDict]:
    header, off = _split_header(buf)
    p = _Parser(buf, path, header)
    p.pos = off
    return header, p.parse_dict_body([], None)


def read_foam_file(path: str | os.PathLike) -> tuple[FoamHeader | None, FoamDict]:
    """Read an OpenFOAM dictionary-format file (including volFields). Returns (header, dict)."""
    return _read(Path(path))


def read_foam_list(path: str | os.PathLike, elem: str) -> tuple[FoamHeader, np.ndarray | tuple]:
    """Read a file whose body is a bare counted list (polyMesh ``points``, ``owner``, ``faces``, ...).

    ``elem`` is ``"label"``, ``"scalar"``, ``"vector"`` or ``"face"``. Faces are returned as
    ``(offsets, labels)`` in compact form for both ASCII ``faceList`` and binary ``faceCompactList``.
    """
    path = Path(path)
    buf = _load_bytes(path)
    header, off = _split_header(buf)
    header = header or FoamHeader()
    p = _Parser(buf, path, header)
    p.pos = off

    def counted(el: str):
        tok = p.read_word()
        while not tok.isdigit():  # skip anything before the count (e.g. a note)
            tok = p.read_word()
        count = int(tok)
        if p.read_word() != b"(":
            raise ValueError(f"{path}: expected '(' after the list size")
        return p.parse_list([], count, el)

    if elem != "face":
        return header, counted(elem)
    if header.format == "binary" or header.cls == "faceCompactList":
        offsets = counted("label").astype(np.int64)
        labels = counted("label").astype(np.int64)
        return header, (offsets, labels)
    # ASCII faceList: N ( n(a b c ...) ... )
    tok = p.read_word()
    while not tok.isdigit():
        tok = p.read_word()
    nfaces = int(tok)
    if p.read_word() != b"(":
        raise ValueError(f"{path}: expected '('")
    p.skip_ws()
    start = p.pos
    view = np.frombuffer(buf, dtype=np.uint8, offset=start)
    closes = np.flatnonzero(view == 0x29)
    end = start + int(closes[nfaces])
    nums = np.array(buf[start:end].replace(b"(", b" ").replace(b")", b" ").split(), dtype=np.int64)
    # Vectorised size-prefix scan: the size of each face is the last number token before its '(' -- count the
    # number tokens that start before every '(' (a token starts at a digit not preceded by a digit).
    body = np.frombuffer(buf, dtype=np.uint8, count=end - start, offset=start)
    digit = (body >= 0x30) & (body <= 0x39)
    tok_start = digit.copy()
    tok_start[1:] &= ~digit[:-1]
    opens = np.flatnonzero(body == 0x28)
    if opens.size != nfaces:
        raise ValueError(f"{path}: {opens.size} face lists for {nfaces} faces")
    size_idx = np.cumsum(tok_start, dtype=np.int64)[opens] - 1
    sizes = nums[size_idx] if nfaces else np.zeros(0, dtype=np.int64)
    if nfaces and (np.any(np.diff(size_idx) != sizes[:-1] + 1) or size_idx[0] != 0
                   or size_idx[-1] + sizes[-1] + 1 != nums.size):
        raise ValueError(f"{path}: malformed ASCII faceList")
    keep = np.ones(nums.size, dtype=bool)
    keep[size_idx] = False
    offsets = np.zeros(nfaces + 1, dtype=np.int64)
    np.cumsum(sizes, out=offsets[1:])
    return header, (offsets, nums[keep])


# ---------------------------------------------------------------------- writer
_BANNER = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "  =========                 |\n"
    "  \\\\      /  F ield         | written by nnfoam\n"
    "   \\\\    /   O peration     |\n"
    "    \\\\  /    A nd           |\n"
    "     \\\\/     M anipulation  |\n"
    "\\*---------------------------------------------------------------------------*/\n"
)


def _fmt_num(x) -> str:
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.10g}"


def _fmt_array(a: np.ndarray, indent: str) -> str:
    a = np.asarray(a)
    if a.ndim == 1:
        if a.size <= 10:
            return "(" + " ".join(_fmt_num(x) for x in a) + ")"
        body = "\n".join(_fmt_num(x) for x in a)
        return f"\n{a.size}\n(\n{body}\n)\n"
    rows = "\n".join("(" + " ".join(_fmt_num(x) for x in r) + ")" for r in a)
    return f"\n{a.shape[0]}\n(\n{rows}\n)\n"


def format_value(v, indent: str = "") -> str:
    if isinstance(v, FoamDict) or isinstance(v, dict):
        inner = indent + "    "
        lines = [f"\n{indent}{{"]
        for k, x in v.items():
            lines.append(_format_entry(k, x, inner))
        lines.append(f"{indent}}}")
        return "\n".join(lines)
    if isinstance(v, FoamString):
        return f'"{v}"'
    if isinstance(v, DimensionSet):
        return "[" + " ".join(_fmt_num(x) for x in v) + "]"
    if isinstance(v, np.ndarray):
        return _fmt_array(v, indent)
    if isinstance(v, tuple):
        return " ".join(format_value(x, indent) for x in v)
    if isinstance(v, list):
        return "(" + " ".join(format_value(x, indent) for x in v) + ")"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float, np.integer, np.floating)):
        return _fmt_num(v)
    return str(v)


def _format_entry(k, v, indent: str) -> str:
    key = f'"{k}"' if isinstance(k, FoamString) else str(k)
    if isinstance(v, (FoamDict, dict)):
        return f"{indent}{key}{format_value(v, indent)}"
    return f"{indent}{key:<16} {format_value(v, indent)};"


def write_foam_file(path: str | os.PathLike, d: dict, cls: str = "dictionary", obj: str | None = None,
                    location: str | None = None) -> None:
    """Write ``d`` as an ASCII OpenFOAM file. Large numeric arrays are written as counted lists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    hdr = ["FoamFile", "{", "    version     2.0;", "    format      ascii;", f"    class       {cls};"]
    if location:
        hdr.append(f'    location    "{location}";')
    hdr.append(f"    object      {obj or path.name};")
    hdr.append("}")
    out = [_BANNER, "\n".join(hdr), "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"]
    for k, v in d.items():
        out.append(_format_entry(k, v, "") + "\n")
    out.append("\n// ************************************************************************* //\n")
    path.write_text("\n".join(out))
