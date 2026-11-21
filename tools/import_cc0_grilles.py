#!/usr/bin/env python3
"""Turn two downloaded CC0 PBR sets into the pool's ACU grille sets.

    python3 tools/import_cc0_grilles.py --vent <Metal_Vent_006_SD dir> --sheet <SheetMetal002_1K-JPG dir>

Writes ``src/frontend/shared/textures/acu-louvre/`` and ``acu-perforated/``
(color / normal / roughness, 512 px, seamless); then run
``tools/gen_pbr_textures.py`` to measure them into ``materials.json`` and
CREDITS.md. The sources are photo-real CC0 sets, not generated ones:

- acu-louvre: 3dtextures.me "Metal Vent 006" (CC0,
  https://3dtextures.me/2022/02/11/metal-vent-006/), a framed aluminium louvre
  over insect mesh. The framed grille is a single object, so the tile is cut
  from its blade field: whole blade periods vertically (found by
  autocorrelation), the blades' own length horizontally, the edges cross-faded
  so the cut repeats without a seam. Where the opacity map is open the mesh's
  dark plenum shows, as on a real supply grille.
- acu-perforated: ambientCG "SheetMetal002" (CC0, https://ambientcg.com/view?id=SheetMetal002),
  a round-hole perforated sheet, already seamless. Its holes live in the
  opacity map; a cabinet face is not see-through here, so each hole is baked in
  as the dark interior behind it (colour), a flat normal and full roughness.

Normal maps are written in the pool's OpenGL convention (+Y green = up); a
DirectX source is detected from its slopes against the height map and flipped.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path

import numpy as np
from PIL import Image

POOL = Path(__file__).resolve().parents[1] / 'src/frontend/shared/textures'
SIZE = 512
FEATHER = 24          # px of cross-fade at a cut edge, at the source resolution
PLENUM = 0.035        # linear grey of the dark interior seen through an opening


def load(path: Path, mode: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert(mode), dtype=np.float64) / 255.0


def jpeg(arr: np.ndarray, mode: str) -> bytes:
    img = Image.fromarray(np.clip(np.rint(arr * 255.0), 0, 255).astype(np.uint8), mode)
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=90, subsampling=0, optimize=True)
    return out.getvalue()


def to_opengl(normal: np.ndarray, height: np.ndarray) -> np.ndarray:
    """Flip green if the map's Y slopes run against the height field (DirectX)."""
    dh = np.gradient(height, axis=0)       # +image-down
    g = normal[..., 1] - 0.5
    mask = np.abs(dh) > 1e-3
    if mask.sum() < 100:
        return normal
    # OpenGL: green points toward image-up, so a surface rising downward (dh>0) tilts +up.
    corr = float(np.corrcoef(g[mask], dh[mask])[0, 1])
    if corr < 0:
        normal = normal.copy()
        normal[..., 1] = 1.0 - normal[..., 1]
    return normal


def feather_wrap(arr: np.ndarray, axis: int, width: int) -> np.ndarray:
    """Cross-fade the last `width` samples into the first along `axis`, then drop them."""
    n = arr.shape[axis]
    head = np.take(arr, range(0, width), axis=axis)
    tail = np.take(arr, range(n - width, n), axis=axis)
    w = np.linspace(0.0, 1.0, width)
    shape = [1] * arr.ndim
    shape[axis] = width
    w = w.reshape(shape)
    blended = tail * (1 - w) + head * w
    body = np.take(arr, range(width, n - width), axis=axis)
    return np.concatenate([blended, body], axis=axis)


def resize(arr: np.ndarray, mode: str) -> np.ndarray:
    img = Image.fromarray(np.clip(np.rint(arr * 255.0), 0, 255).astype(np.uint8), mode)
    return np.asarray(img.resize((SIZE, SIZE), Image.LANCZOS), dtype=np.float64) / 255.0


def blade_period(height: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> int:
    profile = height[y0:y1, x0:x1].mean(axis=1)
    profile = profile - profile.mean()
    ac = np.correlate(profile, profile, mode='full')[len(profile) - 1:]
    lo = 20
    return int(lo + np.argmax(ac[lo:len(profile) // 2]))


def louvre(src: Path) -> dict[str, bytes]:
    stem = 'Metal_Vent_006_'
    color = load(src / f'{stem}basecolor.jpg', 'RGB')
    height = load(src / f'{stem}height.png', 'L')
    rough = load(src / f'{stem}roughness.jpg', 'L')
    opacity = load(src / f'{stem}opacity.jpg', 'L')
    normal = to_opengl(load(src / f'{stem}normal.jpg', 'RGB'), height)
    ys, xs = np.where(opacity > 0.5)
    # The blade field: inside the frame, the flat raised border around the
    # opaque grille. Walk in from each outer edge until the frame ends.
    frame_h = np.percentile(height[ys.min():ys.max(), xs.min():xs.max()], 95)
    cy, cx = (ys.min() + ys.max()) // 2, (xs.min() + xs.max()) // 2

    def inward(line, start, step):
        i = start
        while height_at(line, i) < frame_h - 0.05:   # outside, before the frame
            i += step
        while height_at(line, i) >= frame_h - 0.05:  # across the frame
            i += step
        return i
    col_line, row_line = height[:, cx], height[cy, :]
    height_at = lambda line, i: line[i]  # noqa: E731
    y0, y1 = inward(col_line, ys.min(), 1) + 8, inward(col_line, ys.max(), -1) - 8
    x0, x1 = inward(row_line, xs.min(), 1) + 8, inward(row_line, xs.max(), -1) - 8
    period = blade_period(height, y0, y1, x0, x1)
    # Start at a blade crest and keep whole periods, plus the feather overlap.
    crest = y0 + int(np.argmax(height[y0:y0 + period, x0:x1].mean(axis=1)))
    count = max(1, (y1 - crest - FEATHER) // period)
    rs = slice(crest, crest + count * period + FEATHER)
    cs = slice(x0, x1)
    open_ = (1.0 - opacity[rs, cs])[..., None]
    col = color[rs, cs] * (1 - open_) + PLENUM * open_
    nor = normal[rs, cs] * (1 - open_) + np.array([0.5, 0.5, 1.0]) * open_
    rgh = rough[rs, cs] * (1 - open_[..., 0]) + 1.0 * open_[..., 0]
    out = {}
    for key, arr, mode in (('color', col, 'RGB'), ('normal', nor, 'RGB'), ('roughness', rgh, 'L')):
        a = feather_wrap(arr, 0, FEATHER)
        a = feather_wrap(a, 1, FEATHER)
        out[key] = jpeg(resize(a, mode), mode)
    print(f'acu-louvre: blade period {period} px, {count} blades, crop rows {rs.start}-{rs.stop} cols {x0}-{x1}')
    return out


def perforated(src: Path) -> dict[str, bytes]:
    stem = 'SheetMetal002_1K-JPG_'
    color = load(src / f'{stem}Color.jpg', 'RGB')
    rough = load(src / f'{stem}Roughness.jpg', 'L')
    opacity = load(src / f'{stem}Opacity.jpg', 'L')
    normal = load(src / f'{stem}NormalGL.jpg', 'RGB')
    open_ = (1.0 - opacity)[..., None]
    col = color * (1 - open_) + PLENUM * open_
    nor = normal * (1 - open_) + np.array([0.5, 0.5, 1.0]) * open_
    rgh = rough * (1 - open_[..., 0]) + 1.0 * open_[..., 0]
    return {key: jpeg(resize(arr, mode), mode)
            for key, arr, mode in (('color', col, 'RGB'), ('normal', nor, 'RGB'), ('roughness', rgh, 'L'))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--vent', type=Path, required=True, help='the Metal_Vent_006_SD folder')
    ap.add_argument('--sheet', type=Path, required=True, help='the unzipped SheetMetal002_1K-JPG folder')
    args = ap.parse_args(argv)
    for name, files in (('acu-louvre', louvre(args.vent)), ('acu-perforated', perforated(args.sheet))):
        folder = POOL / name
        folder.mkdir(exist_ok=True)
        for key, data in files.items():
            (folder / f'{key}.jpg').write_bytes(data)
        print(f'{name}: ' + ', '.join(f'{k} {len(v) // 1024} KB' for k, v in files.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
