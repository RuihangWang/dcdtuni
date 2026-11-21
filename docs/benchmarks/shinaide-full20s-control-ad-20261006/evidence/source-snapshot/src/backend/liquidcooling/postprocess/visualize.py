"""Visualize liquid cooling network graph using NetworkX + Matplotlib.

Public API
----------
draw_network_graph(liquidrun_data, output_path, **kwargs)
    Render the network to a PNG file.

compute_layout(liquidrun_data) -> dict[str, tuple[float, float]]
    Return {node_id: (x, y)} positions for the schematic layout.

parse_rack_topology(liquidrun_data) -> dict[str, dict]
    Return per-rack topology: {rack_id: {mf_supply, mf_return, servers: {srv: {in, out, cps}}}}.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx

# ── Colour palette (VSCode-dark inspired) ──────────────────────────────────

_EDGE_COLORS = {
    "pipe": "#98c379",
    "cold_plate": "#e5c07b",
    "pump": "#c678dd",
    "valve": "#56b6c2",
}
_NODE_COLORS = {
    "pressure_bc": "#e06c75",
    "junction": "#61afef",
}
_BG = "#1a1a2e"
_HEADER_BG = "#222238"


# ═══════════════════════════════════════════════════════════════════════════
# Topology parsing
# ═══════════════════════════════════════════════════════════════════════════

def parse_rack_topology(data: dict) -> Dict[str, dict]:
    """Parse liquidrun JSON into per-rack topology.

    Returns {rack_id: {mf_supply, mf_return, servers: {srv_name: {in, out, cps: [node_ids]}}}}.
    """
    racks_cfg = data.get("racks", [])
    nodes = data.get("nodes", [])
    node_ids = {n["id"] for n in nodes}

    result: Dict[str, dict] = {}
    for r in racks_cfg:
        rid = r["id"]
        prefix = rid + "_"
        mf_s = f"{rid}_mf_supply" if f"{rid}_mf_supply" in node_ids else None
        mf_r = f"{rid}_mf_return" if f"{rid}_mf_return" in node_ids else None

        servers: Dict[str, dict] = {}
        for nid in node_ids:
            if not nid.startswith(prefix) or "mf_" in nid:
                continue
            rest = nid[len(prefix):]
            m = re.match(r"(liquid-server_\d+|fan-server_\d+|non-fan_\d+)", rest)
            if not m:
                continue
            srv = m.group(1)
            if srv not in servers:
                servers[srv] = {"in": None, "out": None, "cps": []}
            if rest.endswith("_in"):
                servers[srv]["in"] = nid
            elif rest.endswith("_out") and "cp" not in rest:
                servers[srv]["out"] = nid
            elif "cp" in rest:
                servers[srv]["cps"].append(nid)

        for srv in servers.values():
            srv["cps"].sort(key=lambda n: int(re.search(r"cp(\d+)", n).group(1)))

        result[rid] = {"mf_supply": mf_s, "mf_return": mf_r, "servers": servers}

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Layout
# ═══════════════════════════════════════════════════════════════════════════

def compute_layout(data: dict) -> Dict[str, Tuple[float, float]]:
    """Compute schematic (x, y) positions. CDU on the far left, racks spread right."""
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    racks = parse_rack_topology(data)
    rack_ids = list(racks.keys())

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"])
    for e in edges:
        G.add_edge(e["from"], e["to"], eid=e["id"], etype=e["type"])

    pos: Dict[str, Tuple[float, float]] = {}

    # ── Constants ──
    # Both supply & return mains run along the same side (bottom of racks),
    # matching the physical 3D layout where they are parallel pipes at floor level.
    X_CDU = 0.0
    Y_CDU_SUPPLY = 2.0
    Y_CDU_RETURN = 0.8
    X_RACK_START = 5.0
    RACK_SPACING = 6.0
    Y_PIPE_SUPPLY = 2.0
    Y_PIPE_RETURN = 0.8
    Y_MF = 3.8
    Y_SRV_IO = 5.5
    Y_CP = 7.0
    CP_SPACING = 1.0

    # ── CDU ──
    cdu_supply = [n["id"] for n in nodes if n["type"] == "pressure_bc" and "supply" in n["id"]]
    cdu_return = [n["id"] for n in nodes if n["type"] == "pressure_bc" and "return" in n["id"]]
    for n in cdu_supply:
        pos[n] = (X_CDU, Y_CDU_SUPPLY)
    for n in cdu_return:
        pos[n] = (X_CDU, Y_CDU_RETURN)

    # ── Per-rack positions ──
    rack_x: Dict[str, float] = {}
    for i, rid in enumerate(rack_ids):
        rack_x[rid] = X_RACK_START + i * RACK_SPACING

    # ── Supply main tees (Pipe_1) ──
    tee_to_rack: Dict[str, str] = {}
    for u, v, d in G.edges(data=True):
        eid = d.get("eid", "")
        if eid.startswith("Pipe_1_spoke") or eid.startswith("Pipe_1_seg"):
            for rid in rack_ids:
                if v.startswith(rid):
                    tee_to_rack[u] = rid

    pipe1_tees = sorted(
        [n["id"] for n in nodes if n["id"].startswith("Pipe_1_tee")],
        key=lambda t: rack_x.get(tee_to_rack.get(t, ""), 0),
    )
    pipe1_blinds = [n["id"] for n in nodes if "Pipe_1_blind" in n["id"]]

    for t in pipe1_tees:
        target_rid = tee_to_rack.get(t)
        x = rack_x[target_rid] if target_rid else X_RACK_START
        pos[t] = (x, Y_PIPE_SUPPLY)
    for n in pipe1_blinds:
        pos[n] = (rack_x[rack_ids[-1]] + 2.0, Y_PIPE_SUPPLY) if rack_ids else (X_RACK_START, Y_PIPE_SUPPLY)

    # ── Return main tees (Pipe_2) ──
    tee2_to_rack: Dict[str, str] = {}
    for u, v, d in G.edges(data=True):
        eid = d.get("eid", "")
        if eid.startswith("Pipe_2_spoke"):
            for rid in rack_ids:
                if v.startswith(rid):
                    tee2_to_rack[u] = rid

    pipe2_tees = sorted(
        [n["id"] for n in nodes if n["id"].startswith("Pipe_2_tee") or n["id"].startswith("Pipe_2_valve") or "Pipe_2_tee_valve" in n["id"]],
        key=lambda t: rack_x.get(tee2_to_rack.get(t, ""), 0),
    )
    pipe2_blinds = [n["id"] for n in nodes if "Pipe_2_blind" in n["id"]]

    for t in pipe2_tees:
        target_rid = tee2_to_rack.get(t)
        x = rack_x[target_rid] if target_rid else X_RACK_START
        pos[t] = (x, Y_PIPE_RETURN)
    for n in pipe2_blinds:
        pos[n] = (rack_x[rack_ids[-1]] + 2.0, Y_PIPE_RETURN) if rack_ids else (X_RACK_START, Y_PIPE_RETURN)

    # ── Rack internals ──
    for rid in rack_ids:
        rx = rack_x[rid]
        rd = racks[rid]

        if rd["mf_supply"]:
            pos[rd["mf_supply"]] = (rx - 1.0, Y_MF)
        if rd["mf_return"]:
            pos[rd["mf_return"]] = (rx + 1.0, Y_MF)

        for srv in rd["servers"].values():
            if srv["in"]:
                pos[srv["in"]] = (rx - 1.0, Y_SRV_IO)
            if srv["out"]:
                pos[srv["out"]] = (rx + 1.0, Y_SRV_IO)

            cps = srv["cps"]
            n_cps = len(cps)
            spacing = min(CP_SPACING, 4.0 / max(n_cps, 1))
            start_x = rx - (n_cps - 1) * spacing / 2.0
            for j, cp_n in enumerate(cps):
                pos[cp_n] = (start_x + j * spacing, Y_CP)

    # ── Fallback ──
    for n in nodes:
        if n["id"] not in pos:
            pos[n["id"]] = (X_CDU, (Y_CDU_SUPPLY + Y_CDU_RETURN) / 2)

    return pos


# ═══════════════════════════════════════════════════════════════════════════
# Drawing
# ═══════════════════════════════════════════════════════════════════════════

def _short_label(nid: str) -> str:
    if "CDU" in nid and "supply" in nid:
        return "CDU\nSupply"
    if "CDU" in nid and "return" in nid:
        return "CDU\nReturn"
    if "mf_supply" in nid:
        return "MF\nSupply"
    if "mf_return" in nid:
        return "MF\nReturn"
    if "blind_end" in nid:
        return "Blind\nEnd"
    if "tee_tee" in nid or "tee_valve" in nid:
        return "Tee"
    cp_m = re.search(r"cp(\d+)", nid)
    if cp_m and "_out" in nid:
        return f"CP{cp_m.group(1)}"
    if nid.endswith("_in"):
        return "IN"
    if nid.endswith("_out"):
        return "OUT"
    return nid.split("_")[-1]


def draw_network_graph(
    data: dict,
    output_path: str | Path,
    *,
    dpi: int = 180,
    figsize: Tuple[float, float] | None = None,
) -> Path:
    """Render the liquid cooling network graph to a PNG file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    racks = parse_rack_topology(data)
    rack_ids = list(racks.keys())

    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], ntype=n["type"])
    for e in edges:
        G.add_edge(e["from"], e["to"], eid=e["id"], etype=e["type"],
                   params=e.get("params", {}))

    pos = compute_layout(data)

    if figsize is None:
        w = max(16, len(rack_ids) * 7 + 6)
        figsize = (w, 12)

    fig, ax = plt.subplots(1, 1, figsize=figsize, facecolor=_BG)
    ax.set_facecolor(_BG)

    # ── Rack background boxes ──
    # Use layout constants matching compute_layout
    _Y_MF = 3.8
    _Y_CP = 7.0
    _Y_SRV = 5.5
    for rid in rack_ids:
        rd = racks[rid]
        rack_nodes = [n for n in pos if n.startswith(rid + "_")]
        if not rack_nodes:
            continue
        xs = [pos[n][0] for n in rack_nodes]
        x_min, x_max = min(xs) - 0.8, max(xs) + 0.8
        y_bot, y_top = _Y_MF - 0.5, _Y_CP + 0.5
        rect = plt.Rectangle(
            (x_min, y_bot), x_max - x_min, y_top - y_bot,
            linewidth=1.2, edgecolor="#446688", facecolor="#1e2238",
            alpha=0.5, linestyle="--", zorder=0,
        )
        ax.add_patch(rect)

        short_rid = rid.replace("Default_", "")
        srvs = list(rd["servers"].keys())
        srv_str = ", ".join(
            s.replace("fan-server_", "FS").replace("non-fan_", "NF") for s in srvs
        )
        n_cp = sum(len(s["cps"]) for s in rd["servers"].values())
        center_x = (x_min + x_max) / 2
        ax.annotate(
            f"{short_rid}\n{srv_str}  ({n_cp} CPs)",
            xy=(center_x, y_top + 0.15), fontsize=9, color="#56b6c2",
            fontweight="bold", fontfamily="monospace", ha="center", va="bottom",
            alpha=0.85,
        )

    # ── Edges ──
    for etype, color in _EDGE_COLORS.items():
        edge_list = [(u, v) for u, v, d in G.edges(data=True) if d.get("etype") == etype]
        if not edge_list:
            continue
        width = 3.0 if etype == "cold_plate" else 2.5 if etype == "pump" else 1.6
        rad = 0.25 if etype == "pump" else 0.0
        nx.draw_networkx_edges(
            G, pos, edgelist=edge_list, ax=ax,
            edge_color=color, width=width,
            arrows=True, arrowsize=16, arrowstyle="-|>",
            connectionstyle=f"arc3,rad={rad}",
            alpha=0.88, min_source_margin=14, min_target_margin=14,
        )

    # ── Nodes ──
    for ntype, color in _NODE_COLORS.items():
        node_list = [n for n in G.nodes() if G.nodes[n].get("ntype") == ntype]
        if not node_list:
            continue
        marker = "s" if ntype == "pressure_bc" else "o"
        size = 600 if ntype == "pressure_bc" else 280
        nx.draw_networkx_nodes(
            G, pos, nodelist=node_list, ax=ax,
            node_color=color, node_size=size,
            node_shape=marker, edgecolors="white", linewidths=1.3, alpha=0.95,
        )

    # ── Labels ──
    labels = {n: _short_label(n) for n in G.nodes()}
    nx.draw_networkx_labels(
        G, pos, labels=labels, ax=ax,
        font_size=8, font_color="white", font_family="monospace",
        font_weight="bold",
    )

    # ── Edge labels ──
    edge_labels = {}
    for u, v, d in G.edges(data=True):
        etype = d.get("etype", "")
        eid = d.get("eid", "")
        params = d.get("params", {})
        if etype == "cold_plate":
            cp_m = re.search(r"cp(\d+)", eid)
            label = f"CP{cp_m.group(1)}" if cp_m else "CP"
            power = params.get("Q_W", 0)
            if power > 0:
                label += f"\n{power:.0f}W"
            edge_labels[(u, v)] = label
        elif etype == "pump":
            edge_labels[(u, v)] = "PUMP"

    if edge_labels:
        nx.draw_networkx_edge_labels(
            G, pos, edge_labels=edge_labels, ax=ax,
            font_size=6, font_color="#dcdcaa", font_family="monospace",
            bbox=dict(boxstyle="round,pad=0.1", facecolor="#2a2a4a",
                      edgecolor="none", alpha=0.85),
        )

    # ── Layer annotations (left) ──
    x_left = -3.0
    for y, label, color in [
        (7.0, "Cold Plates", "#e5c07b"),
        (5.5, "Server I/O", "#61afef"),
        (3.8, "Manifold", "#61afef"),
        (2.0, "Supply Main", "#98c379"),
        (1.4, "CDU", "#e06c75"),
        (0.8, "Return Main", "#98c379"),
    ]:
        ax.annotate(
            label, xy=(x_left, y), fontsize=10, color=color,
            fontweight="bold", fontfamily="monospace", alpha=0.65, va="center",
            bbox=dict(boxstyle="round,pad=0.25", facecolor=_BG,
                      edgecolor=color, alpha=0.3, lw=0.8),
        )

    # ── Separators ──
    for y in [2.9, 4.65, 6.25]:
        ax.axhline(y=y, color="#333355", linestyle=":", linewidth=0.7, alpha=0.35)

    # ── Legend (left side, below layer labels) ──
    legend_elements = [
        mpatches.Patch(facecolor="#e06c75", edgecolor="white", label="Pressure BC (CDU)"),
        mpatches.Patch(facecolor="#61afef", edgecolor="white", label="Junction"),
        plt.Line2D([0], [0], color="#98c379", lw=2, label="Pipe"),
        plt.Line2D([0], [0], color="#e5c07b", lw=3, label="Cold Plate"),
        plt.Line2D([0], [0], color="#c678dd", lw=2.5, label="Pump"),
        plt.Line2D([0], [0], color="#56b6c2", lw=2, label="Valve"),
    ]
    ax.legend(
        handles=legend_elements, loc="upper left", fontsize=10,
        facecolor=_HEADER_BG, edgecolor="#555", labelcolor="white",
        framealpha=0.9,
    )

    # ── Title ──
    n_cps = sum(1 for e in edges if e["type"] == "cold_plate")
    n_pumps = sum(1 for e in edges if e["type"] == "pump")
    ax.set_title(
        f"Liquid Cooling Network — {data.get('projectName', '')}\n"
        f"{len(nodes)} nodes · {len(edges)} edges · "
        f"{len(rack_ids)} racks · {n_cps} cold plates · {n_pumps} pumps",
        fontsize=14, color="#00b4dc", fontweight="bold", pad=18,
        fontfamily="monospace",
    )

    ax.axis("off")
    ax.margins(0.06)
    plt.tight_layout()

    fig.savefig(str(output_path), dpi=dpi, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)

    return output_path
