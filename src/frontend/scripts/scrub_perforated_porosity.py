"""Scrub legacy top-level 'porosity' fields from baffle/floor/patch
cfdproj entries and replace with the default perforated block where the
parent toggle says 'perforated'. Idempotent."""
import json
import pathlib
import sys

DEFAULT = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}


def scrub(root: dict) -> int:
    """Mutate root in place, return number of fields changed."""
    changes = 0
    for model in root.get("models", []):
        cfd = model.get("cfd")
        kind = model.get("kind")
        if isinstance(cfd, dict) and kind == "baffle":
            if "porosity" in cfd:
                old = cfd.pop("porosity")
                if cfd.get("baffleType") == "perforated" and old not in (0, None):
                    print(f"  DROPPED porosity={old} on perforated baffle "
                          f"'{model.get('name')}'; replaced with default {DEFAULT}")
                changes += 1
            if cfd.get("baffleType") == "perforated" and "perforated" not in cfd:
                cfd["perforated"] = dict(DEFAULT)
                changes += 1
            for pp_id, pp in (cfd.get("patchPhysics") or {}).items():
                if "porosity" in pp:
                    old = pp.pop("porosity")
                    if pp.get("baffleType") == "perforated" and old not in (0, None):
                        print(f"  DROPPED porosity={old} on perforated baffle patch "
                              f"'{model.get('name')}/{pp_id}'; replaced with default {DEFAULT}")
                    changes += 1
                if pp.get("baffleType") == "perforated" and "perforated" not in pp:
                    pp["perforated"] = dict(DEFAULT)
                    changes += 1
        if isinstance(cfd, dict) and kind == "floor":
            for op in cfd.get("openings", []) or []:
                if "porosity" in op:
                    old = op.pop("porosity")
                    if op.get("type") == "perforated" and old not in (0, None):
                        print(f"  DROPPED porosity={old} on perforated floor opening "
                              f"'{model.get('name')}/{op.get('patchId')}'; replaced with default {DEFAULT}")
                    changes += 1
                if op.get("type") == "perforated" and "perforated" not in op:
                    op["perforated"] = dict(DEFAULT)
                    changes += 1
            # 2026-04-16: floor remaining-area BC also gains a perforated
            # block in place of the legacy scalar `porosity`.
            default_bc = cfd.get("defaultBC")
            if isinstance(default_bc, dict):
                if "porosity" in default_bc:
                    old = default_bc.pop("porosity")
                    if default_bc.get("type") == "perforated" and old not in (0, None):
                        print(f"  DROPPED porosity={old} on perforated floor defaultBC "
                              f"'{model.get('name')}'; replaced with default {DEFAULT}")
                    changes += 1
                if default_bc.get("type") == "perforated" and "perforated" not in default_bc:
                    default_bc["perforated"] = dict(DEFAULT)
                    changes += 1
        # 2026-04-16: rack inlet/outlet doors swap fraction `porosity` for
        # the same `perforated` sub-object as baffles/floors.
        if kind == "rack":
            rack = model.get("rack") or {}
            for door_key in ("inletDoor", "outletDoor"):
                door = rack.get(door_key)
                if not isinstance(door, dict):
                    continue
                if "porosity" in door:
                    old = door.pop("porosity")
                    # Default legacy fraction was 0.5; any other value worth
                    # flagging because the new sub-object's openAreaPct is a
                    # different scale (percent vs fraction) and may surprise
                    # the user on import.
                    if old not in (0, 0.5, None):
                        print(f"  DROPPED porosity={old} on rack door "
                              f"'{model.get('name')}/{door_key}'; replaced with default {DEFAULT}")
                    changes += 1
                if "perforated" not in door:
                    door["perforated"] = dict(DEFAULT)
                    changes += 1
    return changes


def main() -> int:
    proj_dir = pathlib.Path(__file__).resolve().parent.parent / "projects"
    total = 0
    for fp in sorted(proj_dir.glob("*.cfdproj")):
        doc = json.loads(fp.read_text())
        root = doc.get("state", doc)
        n = scrub(root)
        if n:
            fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2))
            print(f"{fp.name}: {n} fields changed")
            total += n
    print(f"total fields changed: {total}")
    return 0 if total >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
