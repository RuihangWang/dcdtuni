"""Build the model-tree manifest the web UI renders, from a parsed .cfdproj scene.

The OV command channel is ONE-WAY: the page fires a command over the WebRTC data
channel and gets no reply, so it cannot ask Kit what the scene contains. Until now the
tree was therefore a HARDCODED list of the GTC scene's groups — twelve rows, seven of
which belong to a scene that is not loaded. Ticking one of those did nothing, which is
exactly what "the Floor checkbox has no effect" was: `Floor` is the GTC group, and a
.cfdproj build creates `Floors`.

So Kit writes this manifest to a JSON file after every build and the dev server serves
it back at /api/scene-tree. That is a side channel, not a reply — the page polls a file
rather than waiting on a channel that has no return path.

Shape mirrors STL_generate.html's tree, which is the thing being replicated:

    Project Root
      📦 Box (BOX)          [2]        <- category, count badge, collapsible
        ☑ 📦 Box_1  [BOX]              <- object, visibility checkbox, kind badge
      🗄️ Rack (RACK)        [4]
        ☑ 🗄️ Default_Rack_1  [RACK]
          📄 Front          [42]       <- face, slot-count badge
            🧰 fan-server-1  [fan-server · 14U]

Pure module: no `pxr`, no Kit imports, so tests can build a manifest from a .cfdproj on
disk and assert its shape.
"""

from __future__ import annotations

import json
import os

# (cfdproj class, USD group name, icon, category label, per-object kind badge).
# Order and labels are the editor's CATEGORY_ORDER + i18n en-US strings, so the two trees
# read the same way round. `boxes` first, `racks` second — not alphabetical.
CATEGORIES = [
    ("boxes", "Boxes", "\U0001F4E6", "Box (BOX)", "BOX"),
    ("racks", "Racks", "\U0001F5C4️", "Rack (RACK)", "RACK"),
    ("acus", "Acus", "❄️", "ACU (ACU)", "ACU"),
    ("walls", "Walls", "\U0001F9F1", "Wall (WALL)", "WALL"),
    ("baffles", "Baffles", "\U0001F4C4", "Baffle (BAFFLE)", "BAFFLE"),
    ("floors", "Floors", "\U0001F7EB", "Floor (FLOOR)", "FLOOR"),
]

# RACK_EQUIPMENT_PRESETS[kind].label
EQUIPMENT_LABEL = {
    "fanServer": "fan-server",
    "nonFan": "non-fan equipment",
    "liquidServer": "liquid-server",
}

FACE_LABEL = {
    "front": "Front", "back": "Back", "left": "Left",
    "right": "Right", "top": "Top", "bottom": "Bottom",
    "inlet": "Inlet", "outlet": "Outlet",
}

# Where Kit drops the manifest and the dev server picks it up. A file, not a log line:
# a 238-rack project serialises to hundreds of KB, which would swamp the Operation Log
# and risk being cut by the SSE tail's byte window.
from .owned_paths import metadata_path, write_owned_json

MANIFEST_PATH = metadata_path("DC_CFD_SCENE_TREE", "scene-tree.json")


def _patch_badge(patch: dict) -> str:
    """'25×100%' for a rect, 'r=12%' for a circle — the editor's pDetail, verbatim.

    Server patches are FRACTIONS of their face, which is why the editor prints them as
    percentages rather than millimetres.
    """
    if patch.get("type") == "circle":
        return f"r={round(float(patch.get('r', 0) or 0) * 100)}%"
    return (f"{round(float(patch.get('w', 0) or 0) * 100)}"
            f"×{round(float(patch.get('h', 0) or 0) * 100)}%")


def _rack_children(p, rid: str) -> list:
    """A rack's children, node for node with getTreeHtmlForModel's rack branch.

        🧰 Equipments  [N]
          ● fan-server_1
            ◻ Inlet
            ◻ Outlet   [1 patch]
              ■ Patch 1  [25×100%]
            ◻ Top / Bottom / Left / Right
        🪟 Partitions  [N]
          ▫ partition_1 (deleted)

    NOT six face nodes: a rack's `geometry.faces` is an empty array in every tracked
    project, so the editor's face loop emits nothing and the rack's real children are
    these two groups. Reading the code without checking the data produced a plausible
    tree the editor does not draw.
    """
    faces_out = []
    for s in p.servers:
        kids = []
        for fid in ("inlet", "outlet", "top", "bottom", "left", "right"):
            fdef = (s.faces or {}).get(fid) or {}
            patches = fdef.get("patches") or []
            kids.append({
                "id": f"{rid}/{s.uid}/{fid}",
                "label": FACE_LABEL[fid],
                "icon": "◻",
                "badge": f"{len(patches)} patch" if patches else "",
                "children": [
                    {"id": f"{rid}/{s.uid}/{fid}/{pt.get('id', i)}",
                     "label": f"Patch {i + 1}",
                     "icon": "●" if pt.get("type") == "circle" else "■",
                     "color": pt.get("color") or "#00cc66",
                     "badge": _patch_badge(pt), "children": []}
                    for i, pt in enumerate(patches)
                ],
            })
        faces_out.append({
            "id": f"{rid}/{s.uid}",
            "label": s.name or s.uid,
            "icon": "●",
            # The editor draws the equipment's own inlet colour as a filled dot.
            "color": ((s.faces or {}).get("inlet") or {}).get("color") or "#4488ff",
            "badge": f"{EQUIPMENT_LABEL.get(s.kind, s.kind or 'equipment')}"
                     f" · {s.slot_occupation}U",
            "children": kids,
        })
    out = [{
        "id": f"{rid}/equipments", "label": "Equipments", "icon": "\U0001F9F0",
        "badge": str(len(p.servers)), "children": faces_out,
    }]
    if p.partitions:
        out.append({
            "id": f"{rid}/partitions", "label": "Partitions", "icon": "\U0001FA9F",
            "badge": str(len(p.partitions)),
            "children": [
                {"id": f"{rid}/partitions/{pt.index}",
                 "label": f"{pt.name}{' (deleted)' if pt.deleted else ''}",
                 "icon": "▫", "badge": "", "children": []}
                for pt in p.partitions
            ],
        })
    return out


def _face_children(p, rid: str) -> list:
    """Box / ACU: one node per face, badged with its patch count (the editor's `else`).

    Wall / baffle / floor take the other branch — their patches hang straight off the
    model with no face node in between.
    """
    faces = p.faces if isinstance(p.faces, dict) else {}
    if p.cls in ("walls", "baffles", "floors"):
        primary = "top" if p.cls == "floors" else "front"
        return [
            {"id": f"{rid}/{primary}/{pt.get('id', i)}",
             "label": pt.get("name") or pt.get("id") or f"patch_{i + 1}",
             "icon": "●", "color": pt.get("color") or "#00cc66",
             "badge": "", "children": []}
            for i, pt in enumerate(((faces.get(primary) or {}).get("patches") or []))
        ]
    out = []
    for fid in ("front", "back", "left", "right", "top", "bottom"):
        fdef = faces.get(fid)
        if not isinstance(fdef, dict):
            continue
        patches = fdef.get("patches") or []
        out.append({
            "id": f"{rid}/{fid}",
            "label": fdef.get("displayName") or fdef.get("label") or FACE_LABEL[fid],
            "icon": "\U0001F4C4",
            "badge": str(len(patches)),
            "children": [
                {"id": f"{rid}/{fid}/{pt.get('id', i)}",
                 "label": pt.get("name") or pt.get("id") or f"patch_{i + 1}",
                 "icon": "●", "color": pt.get("color") or "#00cc66",
                 "badge": "", "children": []}
                for i, pt in enumerate(patches)
            ],
        })
    return out


def build_manifest(scene, groups: dict, project: str = "") -> dict:
    """{categories: [...]} for one parsed cfdproj scene.

    `groups` is usdgen.build_scene's return value; an object appears in the tree only if
    it actually got a prim, so a row can never toggle something that does not exist —
    the failure this whole manifest replaces.
    """
    # Replicate build_scene's de-duplication EXACTLY, in the same order over the same
    # list: a second object named "Rack" becomes "Rack_1" there, and a manifest that
    # kept calling it "Rack" would hand the tree an id whose checkbox toggles the FIRST
    # rack instead. The counter is global across classes there, so it is here too.
    ids, counts = {}, {}
    for p in scene.placements:
        safe = _safe(p.name) or p.uid
        n = counts.get(safe, 0)
        counts[safe] = n + 1
        ids[id(p)] = f"{safe}_{n}" if n else safe

    by_cls = {}
    for p in scene.placements:
        by_cls.setdefault(p.cls, []).append(p)

    cats = []
    for cls, group, icon, label, badge in CATEGORIES:
        items = []
        for p in by_cls.get(cls, []):
            safe = ids[id(p)]
            if safe not in groups:
                continue
            items.append({
                "id": safe,
                "label": p.name,
                "icon": icon,
                "badge": badge,
                # The checkbox must open at the project's own state, not at "on".
                "enabled": bool(p.enabled),
                "opacity": float(p.opacity),
                "children": (_rack_children(p, safe) if cls == "racks"
                             else _face_children(p, safe)),
            })
        if not items:
            continue
        cats.append({
            "id": group, "kind": cls, "icon": icon, "label": label,
            "count": len(items), "items": items,
        })
    return {
        "source": "cfdproj",
        "project": project or scene.name,
        "categories": cats,
        "objects": sum(len(c["items"]) for c in cats),
    }


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in (name or ""))


def write_manifest(manifest: dict, path: str = None) -> str:
    path = path if path is not None else metadata_path("DC_CFD_SCENE_TREE", "scene-tree.json")
    return write_owned_json(path, manifest, "DC_CFD_SCENE_TREE")


# The GTC scene's own groups, in the order the scene JSON builds them. Labels used to
# live in the web bundle as a hardcoded list; they belong here now, so ONE side decides
# what a tree row is and the page can no longer offer a row for a scene it has not
# loaded.
GTC_GROUPS = [
    ("Room", "Room shell", "\U0001F9F1", "structure"),
    ("Floor", "Floor", "\U0001F7EB", "structure"),
    ("Racks", "Server racks", "\U0001F5C4️", "equipment"),
    ("ACUs", "ACU units", "❄️", "equipment"),
    ("Containment", "Cold-aisle containment", "\U0001F4C4", "structure"),
    ("Ducts", "Overhead ducts", "\U0001F4E6", "structure"),
    ("Partitions", "Partitions / pillars", "\U0001F9F1", "structure"),
]


def gtc_manifest(geo_groups: dict, rack_names=(), project: str = "GTC") -> dict:
    """The GTC JSON path's tree, in the same shape as build_manifest's.

    Only groups the build actually produced are listed, so the same rule holds on both
    paths: a row exists because a prim exists.
    """
    cats = []
    for name, label, icon, kind in GTC_GROUPS:
        if name not in geo_groups:
            continue
        items = []
        if name == "Racks":
            items = [{"id": r, "label": r, "icon": "\U0001F5C4️", "badge": "RACK",
                      "enabled": True, "opacity": 1.0, "children": []}
                     for r in rack_names if r in geo_groups]
        cats.append({"id": name, "kind": kind, "icon": icon, "label": label,
                     "count": len(items), "items": items})
    return {"source": "gtc", "project": project, "categories": cats,
            "objects": sum(len(c["items"]) for c in cats)}
