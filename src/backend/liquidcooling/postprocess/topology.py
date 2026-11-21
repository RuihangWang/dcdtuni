"""Graph topology exported beside solved fields, for postprocessing and APIs.

Signed edge flows are positive from edge_from to edge_to. Without the
topology a result consumer cannot tell flow direction in space, compute a
velocity (needs D) or rebuild node in/out balances (report liquid_ui 4.1).
"""
from __future__ import annotations

_EDGE_TYPE_NAMES = {0: "pipe", 1: "cold_plate", 2: "valve", 3: "pump", 4: "heat_exchanger"}


def result_topology(graph) -> dict:
    """edge_from/edge_to (node ids), edge_types, edge_D_m (pipes and valves,
    else None) and node_positions_m (scene metres, y up) of a CompiledGraph."""
    node_ids = list(graph.node_ids)
    diameters = [None] * graph.N_edges
    for indices, params in ((graph.pipe_indices, graph.pipe_params), (graph.valve_indices, graph.valve_params)):
        if indices is None or params is None:
            continue
        for edge, diameter in zip(indices.edge_ids.tolist(), params.D.tolist()):
            diameters[edge] = float(diameter)
    return {
        "edge_from": [node_ids[i] for i in graph.edge_from.tolist()],
        "edge_to": [node_ids[i] for i in graph.edge_to.tolist()],
        "edge_types": [_EDGE_TYPE_NAMES[int(t)] for t in graph.edge_type.tolist()],
        "edge_D_m": diameters,
        "node_positions_m": [[float(v) for v in row] for row in graph.node_position.tolist()],
    }
