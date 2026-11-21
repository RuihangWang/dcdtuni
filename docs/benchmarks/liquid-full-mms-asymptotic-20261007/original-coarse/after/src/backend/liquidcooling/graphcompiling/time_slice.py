"""Read-only static topology with independently owned per-tick schedules."""
import copy


def time_slice(graph, index: int):
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < graph.N_t:
        raise ValueError('time index is outside the compiled horizon')
    result = copy.copy(graph)
    result.N_t = 1

    def select(value):
        if value is None:
            return None
        if value.ndim < 2:
            return value.clone()
        column = 0 if value.shape[1] == 1 else index
        if column >= value.shape[1]:
            raise ValueError('schedule is shorter than the compiled horizon')
        return value[:, column:column + 1].clone()

    for group, field in [('pump_params', 'rpm_ratio'), ('valve_params', 'opening'), ('cp_params', 'Q_W')]:
        params = getattr(graph, group)
        if params is not None:
            params = copy.copy(params)
            setattr(params, field, select(getattr(params, field)))
            setattr(result, group, params)
    result.cell_heat_W = select(graph.cell_heat_W)
    result.bc_T_series = select(graph.bc_T_series)
    for name in ('bc_P_series', 'pump_flow_set', 'cp_dT_set', 'hx_T_set'):
        setattr(result, name, select(getattr(graph, name, None)))
    return result
