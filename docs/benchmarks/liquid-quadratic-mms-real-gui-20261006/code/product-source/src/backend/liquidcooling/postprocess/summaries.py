"""Rack/server summaries from solved fields, shared by batch and runtime paths."""
import numpy as np
from .results import RackSummary, ServerSummary


def build_rack_summaries(graph, state, water_outlet, chip_temp, T_all):
    edge_index = {name: i for i, name in enumerate(graph.edge_str_ids)}
    node_index = {name: i for i, name in enumerate(graph.node_ids)}
    cp_local = {} if graph.cp_indices is None else {
        edge: local for local, edge in enumerate(graph.cp_indices.edge_ids.tolist())
    }
    samples = state['m'].shape[1]  # partial runs must not invent future samples
    summaries = []
    for rack in graph.rack_meta:
        servers, outlet_values, outlet_weights = [], [], []
        total = np.zeros(samples)
        for server_id, cp_ids in zip(rack.server_ids, rack.server_edge_ids):
            edges = [edge_index[e] for e in cp_ids if e in edge_index and edge_index[e] in cp_local]
            if not edges:
                continue
            # The graph compiler connects a server's cold plates in SERIES.
            # Count its branch flow once, not once per plate.
            signed_flow = state['m'][edges[0]]
            flow = np.abs(signed_flow)
            temperatures = [chip_temp[cp_local[e]] for e in edges]
            heat = np.zeros(samples)
            for edge in edges:
                local = cp_local[edge]
                upstream = np.where(state['m'][edge] >= 0,
                                    T_all[int(graph.edge_from[edge])],
                                    T_all[int(graph.edge_to[edge])])
                heat += np.abs(state['m'][edge]) * graph.fluid_cp * (water_outlet[local] - upstream)
            outlet = np.where(signed_flow >= 0, water_outlet[cp_local[edges[-1]]],
                              water_outlet[cp_local[edges[0]]])
            servers.append(ServerSummary(server_id, flow, np.max(temperatures, axis=0), heat,
                                         [graph.edge_str_ids[e] for e in edges]))
            total += flow
            outlet_values.append(outlet)
            outlet_weights.append(flow)
        fallback = np.full(samples, graph.T_fluid_init)
        supply_index = node_index.get(f'{rack.rack_id}_mf_supply')
        return_index = node_index.get(f'{rack.rack_id}_mf_return')
        supply = fallback if supply_index is None else T_all[supply_index]
        if return_index is not None:
            returned = T_all[return_index]
        elif outlet_values:
            weight = np.sum(outlet_weights, axis=0)
            returned = np.divide(np.sum(np.array(outlet_values) * outlet_weights, axis=0), weight,
                                 out=fallback.copy(), where=weight > 0)
        else:
            returned = fallback
        summaries.append(RackSummary(rack.rack_id, total, supply, returned, servers))
    return summaries


def summaries_json(summaries):
    return [{'rack_id': r.rack_id, 'total_flow_kg_s': r.total_flow_kg_s.tolist(),
             'supply_temp_K': r.supply_temp_K.tolist(), 'return_temp_K': r.return_temp_K.tolist(),
             'servers': [{'server_id': s.server_id, 'flow_kg_s': s.flow_kg_s.tolist(),
                          'max_chip_temp_K': s.max_chip_temp_K.tolist(),
                          'heat_removed_W': s.heat_removed_W.tolist(), 'cp_edge_ids': s.cp_edge_ids}
                         for s in r.servers]} for r in summaries]
