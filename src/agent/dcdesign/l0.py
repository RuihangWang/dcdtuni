"""Low-fidelity (L0) evaluation of a compiled hall design: zonal air network + DAE liquid side + shared plant.

Air side (``backend.aircooling.zonal``): nine well-mixed zones above the raised
floor -- the cold aisle and the two rear zones, each split at mid rack height,
the two row-end regions (lumped, two levels) and the space above the racks --
plus the floor plenum in the perimeter families. Each server is a fan stream
from its cold-aisle level to its row's rear-zone level carrying its air heat
(IT power minus its cold-plate heat). Each running cooling unit draws from its
return zone through a return node and discharges at its imposed supply
temperature through a supply node (in-row: into the cold aisle; perimeter: into
the plenum, which feeds the cold aisle through the perforated tiles).
Containment panels are leaky walls (declared leakage fraction); rack rows leak
between the cold aisle and the rear zones. Areas come from the hall geometry
recorded by the family builder; the discharge coefficient and the leakage
fractions are declared, not calibrated against CFD.

Liquid side: the product's DAE steady state (``agent.scripted_api.liquid``), which
gives the heat into the CDU primary circuit, the primary return temperature and
the pump hydraulic power.

Plant: ``agent.plant.energy.facility_power`` with every ACU, the CDU primary
branch and the secondary pump; the objective is the design PUE.

A running unit whose supply setpoint is above the air it draws (possible at
partial load, where the return zones are cool) would have to heat. A cooling coil
cannot do that, so the plant sees that unit with its coil off (its return taken
equal to its supply), and the constraint ``unit_heating`` (setpoint minus return,
K, of the worst running unit) reports by how much the setpoint is unattainable.
The air network still imposes the setpoint; a design with ``unit_heating`` > 0 is
infeasible.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import torch

from backend.aircooling.zonal import ZonalNetwork
from agent.plant import energy as E

from .families import aabb
from .spec import CATALOG, FAMILIES, THERMAL_CLASSES

L0_VERSION = "dc-design-l0/v2-2026-10-21"
LEAKAGE = {"rack_row": 0.02, "containment": 0.02}   # fraction of the face area that leaks; declared
MIXING_M3S_PER_M2 = 0.01                            # interzonal exchange through open faces; declared
THERMAL_FLOW_SMOOTHING_M3S = 1e-3                   # width of the smoothed upwind switch (numerical)
PUMP_EFFICIENCY = 0.5                               # secondary pump wire-to-water; declared
CHIP_LIMIT_C = 85.0                                 # cold-plate solid temperature limit; declared
U_M = 0.04445


@dataclass
class HallNetwork:
    network: ZonalNetwork
    areas: torch.Tensor
    unit_names: tuple[str, ...]          # running units, in the order of the controls
    all_units: tuple[str, ...]
    server_ids: tuple[str, ...]
    server_it_kw: torch.Tensor
    server_air_kw: torch.Tensor
    liquid_kw: float
    nominal_controls: torch.Tensor       # supply temperatures then flows (m3/s) of the running units
    family: str


def _track_values(project) -> dict:
    return {(t["modelUid"], t["param"], t["target"]): t["keyframes"][0]["value"] for t in project["transient"]["tracks"]}


def build_network(project: dict) -> HallNetwork:
    meta = project["meta"]["dcdesign"]
    fam = meta["family"]
    kind, cont = FAMILIES[fam]["units"], FAMILIES[fam]["containment"]
    hall = meta["hall"]
    fy, ceil, top = hall["floor_y_m"], hall["ceiling_y_m"], hall["rack_top_y_m"]
    mid = 0.5 * (fy + top)
    x0, x1 = hall["row_x_m"]
    L = x1 - x0
    rx0, rx1 = hall["room_x_m"]
    rz0, rz1 = hall["room_z_m"]
    ca0, ca1 = hall["cold_aisle_z_m"]
    w_ca = ca1 - ca0
    depth = [z1 - z0 for z0, z1 in hall["rear_zones_z_m"]]
    end_area = (rx1 - rx0 - L) * (rz1 - rz0)
    h_lv = (mid - fy, top - mid)
    leak_c = LEAKAGE["containment"]

    names = ["CA_lo", "CA_hi", "RZ0_lo", "RZ0_hi", "RZ1_lo", "RZ1_hi", "END_lo", "END_hi", "ABOVE"]
    heights = [0.5 * (fy + mid), 0.5 * (mid + top)] * 4 + [0.5 * (top + ceil)]
    if kind == "perimeter":
        names.append("PLENUM")
        heights.append(0.5 * fy)
    idx = {n: i for i, n in enumerate(names)}
    pairs, directed, areas = [], [], []

    def link(a, b, area, oneway=False):
        if area > 1e-12:
            pairs.append((idx[a], idx[b]))
            directed.append(oneway)
            areas.append(float(area))

    def node(name, h):
        idx[name] = len(names)
        names.append(name)
        heights.append(h)

    link("CA_lo", "CA_hi", L * w_ca)
    link("END_lo", "END_hi", end_area)
    link("END_hi", "ABOVE", end_area)
    link("CA_hi", "ABOVE", L * w_ca * (leak_c if cont == "cold" else 1.0))
    for k in (0, 1):
        link(f"RZ{k}_lo", f"RZ{k}_hi", L * depth[k])
        link(f"RZ{k}_hi", "ABOVE", L * depth[k] * (leak_c if cont == "hot" else 1.0))
    for lv, h in zip(("lo", "hi"), h_lv):
        link(f"CA_{lv}", f"END_{lv}", 2 * w_ca * h * (leak_c if cont == "cold" else 1.0))
        for k in (0, 1):
            link(f"RZ{k}_{lv}", f"END_{lv}", 2 * depth[k] * h * (leak_c if cont == "hot" else 1.0))
            link(f"CA_{lv}", f"RZ{k}_{lv}", L * h * LEAKAGE["rack_row"])
    if kind == "perimeter":
        t = CATALOG["tile_m"]
        link("PLENUM", "CA_lo", meta["tile_count"] * t * t * CATALOG["tile_open_area_pct"] / 100.0)

    # servers: rows by their rack's z extent; level by the server's centre height
    tracks = _track_values(project)
    row_z = [r["z_m"] for r in hall["rows"]]
    cp_heat = defaultdict(float)
    for (uid, param, target), v in tracks.items():
        if param == "cp.heat":
            cp_heat[(uid, target.split("/")[0])] += v
    s_pairs, s_flow, s_it, s_air, s_ids = [], [], [], [], []
    for r in project["constructions"]["racks"].values():
        b = aabb(r["geometry"])
        k = next(i for i, (z0, z1) in enumerate(row_z) if abs(b[4] - z0) < 1e-6 and abs(b[5] - z1) < 1e-6)
        base = r["geometry"]["location"]["y"] + r.get("safeMarginBottom", U_M)
        for sid, s in r["constructions"]["servers"].items():
            g = s["geometry"]
            yc = base + (g["slotPosition"] - 1 + 0.5 * g["slotOccupation"]) * U_M
            lv = "lo" if yc < mid else "hi"
            it = tracks[(r["uid"], "server.power", sid)] / 1000.0
            air = it - cp_heat[(r["uid"], sid)] / 1000.0
            if air < -1e-9:
                raise ValueError(f"server {sid}: cold-plate heat exceeds its IT power")
            s_pairs.append((idx[f"CA_{lv}"], idx[f"RZ{k}_{lv}"]))
            s_flow.append(float(s["cooling"]["volumeFlowRate"]))
            s_it.append(it)
            s_air.append(max(air, 0.0))
            s_ids.append(f"{r['uid']}/{sid}")

    # cooling units
    closed = set(meta.get("closed_units", []))
    by_name = {a["meta"]["name"]: a for a in project["constructions"]["acus"].values()}
    u_pairs, running, t_sup, flows = [], [], [], []
    for name in meta["units"]:
        if name in closed:
            continue
        a = by_name[name]
        g = a["geometry"]
        sp = g["supplyFaces"][0]["patchId"]
        bx = aabb(g)
        k = int(round(0.5 * (bx[4] + bx[5]) > 0.5 * (ca0 + ca1)))   # row 0 is the low-z side
        ret_patch = next(p for p in g["faces"]["back"]["patches"] if p["id"] == g["returnFaces"][0]["patchId"])
        r_area = ret_patch["w"] * ret_patch["h"]
        node(f"RET_{name}", 0.5 * (fy + bx[3]))
        for lv in ("lo", "hi"):
            link(f"RZ{k}_{lv}", f"RET_{name}", 0.5 * r_area, oneway=True)
        if kind == "inrow":
            s_patch = next(p for p in g["faces"]["front"]["patches"] if p["id"] == sp)
            s_area = s_patch["w"] * s_patch["h"]
            node(f"SUP_{name}", 0.5 * (fy + bx[3]))
            for lv in ("lo", "hi"):
                link(f"SUP_{name}", f"CA_{lv}", 0.5 * s_area, oneway=True)
        else:
            s_patch = next(p for p in g["faces"]["bottom"]["patches"] if p["id"] == sp)
            node(f"SUP_{name}", fy)
            link(f"SUP_{name}", "PLENUM", s_patch["w"] * s_patch["h"], oneway=True)
        u_pairs.append((idx[f"RET_{name}"], idx[f"SUP_{name}"]))
        running.append(name)
        t_sup.append(tracks[(a["uid"], "acu.supplyTemp", sp)])
        flows.append(tracks[(a["uid"], "acu.airflow", sp)])

    net = ZonalNetwork(tuple(names), tuple(heights), tuple(pairs), tuple(directed), tuple(s_pairs),
                       tuple(s_flow), tuple(s_air), tuple(u_pairs), mixing_m3s_per_m2=MIXING_M3S_PER_M2,
                       thermal_flow_smoothing_m3s=THERMAL_FLOW_SMOOTHING_M3S)
    return HallNetwork(net, torch.tensor(areas, dtype=torch.float64), tuple(running), tuple(meta["units"]),
                       tuple(s_ids), torch.tensor(s_it, dtype=torch.float64),
                       torch.tensor(s_air, dtype=torch.float64), sum(cp_heat.values()) / 1000.0,
                       torch.tensor(t_sup + flows, dtype=torch.float64), fam)


def fans() -> E.FanParams:
    return E.FanParams(rated_flow_m3s=CATALOG["acu_rated_flow_m3s"], rated_power_kw=CATALOG["acu_fan_kw_at_rated"],
                       exponent=CATALOG["acu_fan_exponent"], min_fraction=CATALOG["acu_min_flow_fraction"],
                       provenance=CATALOG["provenance"])


def evaluate(project: dict, liquid: dict, *, controls: torch.Tensor | None = None, thermal_class: str = "recommended",
             differentiable: bool = False, primary_inlet_c=None, plant: E.PlantParams | None = None) -> dict:
    """L0 metrics of one compiled design.

    ``liquid`` is ``agent.scripted_api.liquid.solve_liquid_steady``'s result for the same project. ``controls``
    (supply temperatures then flows of the running units, float64) default to the project's; pass a tensor
    that requires grad with ``differentiable=True`` to get gradients of the returned tensors. ``liquid``'s
    primary return temperature, pump power and hottest cold plate, and ``primary_inlet_c`` (default: the
    project's CDU primary inlet), may be tensors too, so a caller can chain the DAE's own gradients.
    ``plant`` replaces the shared plant's parameters (default ``PlantParams()``) for this call only; the
    high-fidelity scoring in ``agent.dcdesign.hf`` keeps the default.
    """
    hn = build_network(project)
    c = hn.nominal_controls if controls is None else controls
    net = hn.network
    state = net.differentiable_state(c, hn.areas) if differentiable else net.solve(c, hn.areas)[0]
    T = net.temperatures(state, c, hn.areas)
    nu = net.n_units
    # every unit of the installed set appears in the plant ledger; closed ones with zero flow
    order = {n: i for i, n in enumerate(hn.unit_names)}
    zero = c[:1] * 0
    flow_all = torch.stack([c[nu + order[n]] if n in order else zero[0] for n in hn.all_units])
    sup_all = torch.stack([c[order[n]] if n in order else zero[0] for n in hn.all_units])
    # a unit whose setpoint is above its return would heat: the plant sees its coil off (constraint unit_heating)
    ret_all = torch.stack([torch.maximum(T["unit_return_c"][order[n]], c[order[n]]) if n in order else zero[0]
                           for n in hn.all_units])
    active = [n in order for n in hn.all_units]
    m = liquid["metrics"]
    hx = next(iter(m["primaryReturnK"]))
    primary_in_c = _primary_inlet_c(project) if primary_inlet_c is None else primary_inlet_c
    it_kw = float(hn.server_it_kw.sum())
    liquid_kw = hn.liquid_kw
    f64 = lambda v: torch.as_tensor(v, dtype=torch.float64).reshape(1)  # noqa: E731  (keeps autograd graphs)
    pump_kw = f64(liquid["objectives"]["pump_hydraulic_power_W"]) / 1000.0 / PUMP_EFFICIENCY
    plant = E.facility_power(
        torch.tensor(it_kw, dtype=torch.float64), it_kw - liquid_kw, flow_all, sup_all, ret_all,
        [fans() for _ in hn.all_units], acu_active=active,
        liquid_heat_kw=f64(liquid_kw), liquid_t_supply_c=f64(primary_in_c),
        liquid_t_return_c=f64(m["primaryReturnK"][hx]) - 273.15, secondary_pump_kw=pump_kw,
        plant=E.PlantParams() if plant is None else plant)
    env = THERMAL_CLASSES[thermal_class]
    inlet = T["server_inlet_c"]
    coil = T["unit_coil_kw"]
    constraints = {
        "server_inlet_max": inlet.max() - env["inlet_max_c"],
        "server_inlet_min": env["inlet_min_c"] - inlet.min(),
        "unit_capacity": (coil.max() - CATALOG["acu_capacity_kw"]) / CATALOG["acu_capacity_kw"],
        "unit_heating": (c[:nu] - T["unit_return_c"]).max(),
        "chip_temperature": torch.as_tensor(liquid["metrics"]["maxChipTemperatureK"], dtype=torch.float64)
        - 273.15 - CHIP_LIMIT_C,
    }
    return {
        "version": L0_VERSION, "family": hn.family, "pue": plant["pue"], "total_kw": plant["total_kw"],
        "plant": plant, "constraints": constraints,
        "server_inlet_c": inlet,
        "server_exhaust_c": inlet + hn.server_air_kw * 1000.0 / (net.rho * net.cp * inlet.new_tensor(net.server_flow_m3s)),
        "unit_return_c": T["unit_return_c"],
        "unit_coil_kw": coil, "zones_c": dict(zip(net.node_names, T["node_c"])),
        "air_heat_closure_kw": coil.sum() - (it_kw - liquid_kw),
        "dae_heat_input_kw": m["heatInputW"] / 1000.0, "declared_liquid_kw": liquid_kw,
        "units": hn.unit_names,
    }


def _primary_inlet_c(project) -> float:
    cdu = next(iter(project["constructions"]["cdus"].values()))["uid"]
    return float(_track_values(project)[(cdu, "cdu.primaryInletT", "")])
