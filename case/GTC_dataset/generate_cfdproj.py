#!/usr/bin/env python3
"""Read sampling_plan.json + GTC v4 template; write per-case cfdproj files."""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from server_types_gtc import (
    ACU_COOLANT_TEMP_C, ACU_COOLING_CAPACITY_KW, ACU_SET_POINT_TEMP_C,
    DATASET_ROOT, TEMPLATE_CFDPROJ,
)


def _proto_server(template: dict) -> dict:
    for rack in template["constructions"]["racks"].values():
        servers = rack.get("constructions", {}).get("servers", {})
        if servers:
            return copy.deepcopy(next(iter(servers.values())))
    raise RuntimeError("template has no prototype server to clone")


def _make_server(uid: str, name: str, slot_position: int, slot_occupation: int,
                 power_w: float, flow_m3s: float, patch_id: str, proto: dict) -> dict:
    sv = copy.deepcopy(proto)
    sv["uid"] = uid
    sv["meta"] = {"name": name, "enabled": True}
    sv["geometry"] = {"slotPosition": slot_position, "slotOccupation": slot_occupation}
    sv["cooling"] = {"volumeFlowRate": flow_m3s, "power": power_w, "fixedTemperature": None}
    # keep the prototype's outlet patch shape but give it a unique id
    outlet_patches = sv.get("faces", {}).get("outlet", {}).get("patches", [])
    if outlet_patches:
        outlet_patches[0] = copy.deepcopy(outlet_patches[0])
        outlet_patches[0]["id"] = patch_id
    return sv


def _clean_cutouts(proj: dict, removed_rack_names: set) -> None:
    """Drop cutouts that reference deleted racks (else floors/baffles keep holes
    where a rack used to be). Cutouts for surviving objects (cracs, pillars) stay."""
    for grp in proj.get("constructions", {}).values():
        if not isinstance(grp, dict):
            continue
        for obj in grp.values():
            if not isinstance(obj, dict):
                continue
            cutouts = obj.get("geometry", {}).get("cutouts")
            if isinstance(cutouts, list) and cutouts:
                obj["geometry"]["cutouts"] = [
                    c for c in cutouts if c.get("sourceName") not in removed_rack_names
                ]


# Display opacity by object kind: equipment (racks, ACUs) opaque so heat sources and
# cooling units read clearly; enclosure/structure (baffles, floors, walls, pillars)
# semi-transparent so they do not hide the interior.
OPACITY_BY_GROUP = {
    "racks": 1.0, "acus": 1.0,
    "baffles": 0.5, "floors": 0.5, "walls": 0.5, "boxes": 0.5,
}


def apply_visibility(proj: dict) -> None:
    """Make geometry visible (template defaults every object to opacity 0 = invisible).

    The frontend reads opacity from TWO places: 3D boxes (racks/ACUs, geometry.faces=[])
    fall back to meta.display.opacity, while planar objects (baffles/floors/walls) read
    per-face geometry.faces[*].opacity + wallOpacities, which SHADOW meta.display.opacity
    (0 is not nullish, so '??' never falls through). So set both. Emptying wallOpacities
    makes the renderer fall back to face.opacity for every wall part of a cut slab.
    (browser-verified: per-face opacity is what reveals baffles/floors.)"""
    con = proj.get("constructions", {})
    for grp, objs in con.items():
        op = OPACITY_BY_GROUP.get(grp)
        if op is None or not isinstance(objs, dict):
            continue
        for obj in objs.values():
            if not isinstance(obj, dict):
                continue
            obj.setdefault("meta", {}).setdefault("display", {})["opacity"] = op
            faces = obj.get("geometry", {}).get("faces")
            if isinstance(faces, dict):
                for face in faces.values():
                    if isinstance(face, dict):
                        face["opacity"] = op
                        face["wallOpacities"] = []


def apply_racks(proj: dict, case_plan: dict) -> None:
    present = set(case_plan["present_rack_uids"])
    racks = proj["constructions"]["racks"]
    proto = _proto_server(proj)

    removed_rack_names = {r["meta"]["name"] for uid, r in racks.items() if uid not in present}

    for uid in list(racks.keys()):
        if uid not in present:
            del racks[uid]

    # Server uids must be UNIQUE PROJECT-WIDE (the cfdrun export rejects duplicates —
    # bySource join + inputs.servers are uid-keyed). Use one running counter across all
    # racks, matching the editor's convention (rack m_1 -> e_1..e_n, m_2 -> e_(n+1).., ...).
    gsrv = 1
    for uid in racks:  # present racks only (absent already deleted), in cfdproj order
        rack = racks[uid]
        rack_name = rack["meta"]["name"]
        rack.setdefault("constructions", {})
        new_servers = {}
        for si, srv in enumerate(case_plan["racks"].get(uid, {}).get("servers", [])):
            sid = f"e_{gsrv}"
            pid = f"p_{uid}_{si + 1}"
            new_servers[sid] = _make_server(
                sid, f"{rack_name}_srv{si + 1}",
                srv["start_u"], srv["span_u"], srv["power_W"], srv["flow_m3s"],
                pid, proto)
            gsrv += 1
        rack["constructions"]["servers"] = new_servers

        # Outlet (hot-aisle) blanking. The template stores deletedFaces.back[i]=false on
        # EMPTY back slots, which the frontend exports as blanking baffles at the outlet.
        # Void only the empty slots (back[i]=True) so no blanking panel is emitted, but
        # KEEP back[i]=False on slots occupied by a server (slotPosition..+slotOccupation-1,
        # 0-based, same index as deletedFaces.back) — that face hosts the server's outlet
        # patch (airflow BC), and deleting it would drop the patch. Inlet (front) untouched.
        df = rack.get("deletedFaces")
        if isinstance(df, dict) and isinstance(df.get("back"), list):
            occupied = set()
            for sv in new_servers.values():
                g = sv.get("geometry", {})
                start = int(g.get("slotPosition", 0))
                span = int(g.get("slotOccupation", 1))
                occupied.update(range(start, start + span))
            df["back"] = [i not in occupied for i in range(len(df["back"]))]

    _clean_cutouts(proj, removed_rack_names)


def apply_acus(proj: dict, case_plan: dict) -> None:
    acu_cfg = case_plan["acu"]
    active = set(acu_cfg["active_uids"])
    supply_temp = acu_cfg["supply_temp_C"]
    flow_rate = acu_cfg["flow_rate_m3s"]

    for uid, acu in proj["constructions"]["acus"].items():
        faces = acu["geometry"]["faces"]
        if uid in active:
            for face in faces.values():
                for patch in face.get("patches", []):
                    phys = patch.setdefault("physics", {})
                    if phys.get("role") == "supply":
                        phys["temperature"] = supply_temp
                        phys["volumeFlowRate"] = flow_rate
                        phys["coolingCapacity"] = ACU_COOLING_CAPACITY_KW
                        phys["coolantTemperature"] = ACU_COOLANT_TEMP_C
                        phys["setPointTemperature"] = ACU_SET_POINT_TEMP_C
                    elif phys.get("role") == "return":
                        phys["volumeFlowRate"] = flow_rate
            acu["meta"]["enabled"] = True
        else:
            # OFF: keep the CRAC box, remove all airflow patches
            for face in faces.values():
                face["patches"] = []
            acu["meta"]["enabled"] = True


def apply_paths(proj: dict, case_id: str, case_dir: str) -> None:
    idx = case_id.split("_")[1]
    meta = proj.setdefault("meta", {})
    meta["casePath"] = case_dir
    meta["projectName"] = f"GTC_dataset_{idx}"
    meta["exportFileName"] = f"GTC_dataset_{idx}"


def modify_cfdproj(template: dict, case_id: str, case_plan: dict, output_dir: str) -> dict:
    proj = copy.deepcopy(template)
    apply_racks(proj, case_plan)
    apply_acus(proj, case_plan)
    apply_visibility(proj)
    apply_paths(proj, case_id, f"{output_dir}/{case_id}")
    return proj


def main():
    p = argparse.ArgumentParser(description="Generate GTC cfdproj files from plan")
    p.add_argument("--plan", type=str, default=f"{DATASET_ROOT}/meta/sampling_plan.json")
    p.add_argument("--template", type=str, default=TEMPLATE_CFDPROJ)
    p.add_argument("--output-dir", type=str, default=DATASET_ROOT)
    p.add_argument("--cases", type=str, default=None, help="comma-separated case ids")
    args = p.parse_args()

    plan = json.load(open(args.plan))
    template = json.load(open(args.template))
    out_root = Path(args.output_dir)

    case_ids = [c.strip() for c in args.cases.split(",")] if args.cases else list(plan.keys())
    generated = 0
    for case_id in case_ids:
        if case_id not in plan:
            print(f"WARNING: {case_id} not in plan, skipping")
            continue
        case_dir = out_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        proj = modify_cfdproj(template, case_id, plan[case_id], str(out_root))
        idx = case_id.split("_")[1]
        (case_dir / f"GTC_dataset_{idx}.cfdproj").write_text(json.dumps(proj, indent=2))
        generated += 1
        if generated % 100 == 0:
            print(f"  Generated {generated}/{len(case_ids)}...")
    print(f"Done: {generated} cfdproj files written to {out_root}")


if __name__ == "__main__":
    main()
