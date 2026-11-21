"""Normalize any stale cfdrun files whose room.plane stores the face-local
2nd coord in p.z (world-Z) to the canonical convention used by the frontend
+ runner: face-local 2nd coord in p.y.

Under Y-up the frontend exports `plane[i] = {x, y: <2nd>, z: 0}`
(inferRoomPolygonFromWalls at STL_generate.html:28902-28915). Older cfdrun
files written by hand or by an earlier script use `{x, y: 0, z: <2nd>}`
which is invisible to the runner (it reads p.y → world Z).

This script detects the p.z style and swaps p.y ↔ p.z so the runner
reads the polygon correctly.
"""
import json
from pathlib import Path


def _plane_uses_p_z(plane: list) -> bool:
    if len(plane) < 2:
        return False
    y_vary = any(abs(p.get("y", 0) - plane[0].get("y", 0)) > 1e-6 for p in plane)
    z_vary = any(abs(p.get("z", 0) - plane[0].get("z", 0)) > 1e-6 for p in plane)
    return z_vary and not y_vary


def normalize_file(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    plane = data.get("room", {}).get("plane")
    if not plane or not _plane_uses_p_z(plane):
        return False
    # Backup
    path.with_suffix(path.suffix + ".bak").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for p in plane:
        p["y"], p["z"] = p.get("z", 0), p.get("y", 0)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return True


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    targets = list(repo.glob("case/*/model/*.cfdrun"))
    changed = 0
    for t in targets:
        if normalize_file(t):
            print(f"normalized: {t.relative_to(repo)}")
            changed += 1
    print(f"total normalized: {changed}/{len(targets)}")


if __name__ == "__main__":
    main()
