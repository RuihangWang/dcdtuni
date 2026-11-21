"""Tests for liquid network graph visualization."""
import json
import os
import tempfile
from pathlib import Path

import pytest

# Frozen copy of the Liquid case's export (case/*/Liquid is gitignored), kept
# next to this file so the path does not depend on the checkout's name.
LIQUIDRUN_PATH = Path(__file__).resolve().parent / "liquid_cooling_demo.liquidrun"


@pytest.fixture
def liquidrun_data():
    with open(LIQUIDRUN_PATH) as f:
        return json.load(f)


@pytest.fixture
def output_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def test_draw_network_creates_png(liquidrun_data, output_dir):
    from backend.liquidcooling.postprocess.visualize import draw_network_graph

    out = output_dir / "graph.png"
    draw_network_graph(liquidrun_data, out)

    assert out.exists()
    assert out.stat().st_size > 10_000  # non-trivial image


def test_draw_network_node_positions_cdu_leftmost(liquidrun_data, output_dir):
    """CDU nodes must be placed at the leftmost x position."""
    from backend.liquidcooling.postprocess.visualize import compute_layout

    pos = compute_layout(liquidrun_data)
    cdu_xs = [pos[n][0] for n in pos if "CDU" in n]
    other_xs = [pos[n][0] for n in pos if "CDU" not in n]

    assert len(cdu_xs) > 0
    assert max(cdu_xs) < min(other_xs)


def test_draw_network_racks_grouped(liquidrun_data, output_dir):
    """Nodes belonging to the same rack should share similar x positions."""
    from backend.liquidcooling.postprocess.visualize import compute_layout

    pos = compute_layout(liquidrun_data)

    rack_ids = set()
    for n in pos:
        for prefix in ["Default_Rack_1", "Default_Rack_2", "Default_Rack_3", "Default_Rack_4"]:
            if n.startswith(prefix):
                rack_ids.add(prefix)

    rack_centers = {}
    for rid in rack_ids:
        xs = [pos[n][0] for n in pos if n.startswith(rid)]
        rack_centers[rid] = sum(xs) / len(xs)

    centers = sorted(rack_centers.values())
    for i in range(len(centers) - 1):
        assert centers[i + 1] - centers[i] > 1.0, "Racks should be spaced apart"


def test_draw_network_cp_count_per_rack(liquidrun_data):
    """Verify the graph has expected CP counts per rack."""
    from backend.liquidcooling.postprocess.visualize import parse_rack_topology

    racks = parse_rack_topology(liquidrun_data)
    assert len(racks) == 4
    cp_counts = sorted(
        sum(len(s["cps"]) for s in r["servers"].values())
        for r in racks.values()
    )
    assert cp_counts == [1, 2, 3, 3]
