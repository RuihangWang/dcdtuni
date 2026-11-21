"""Zonal LF air networks of the slab in-row Shinaide families (OPEN, CAC, HAC) on the exported geometry.

Ports ``agent_legacy/scenario_hybrid/air_network_open_v2.py``, ``air_network_cac_v1.py``
and ``air_network_hac_v1.py`` (three near-copies) into one builder. The passages, their
order and every area are the legacy's, so the operator and its root are the same; the
legacy residual stays the acceptance test (full residual <= 1e-10).

The network is an unfitted five-zone pressure/enthalpy model, not a CFD field:

- five region nodes: cold-lower, cold-upper, hot-lower, hot-upper (split at the
  equipment mid-height) and above-equipment (to the ceiling);
- every server is a fixed-flow stream from its exported intake solid to its exhaust
  solid, carrying its air heat (``Q_IT - Q_liquid`` from the fan contract); each port
  opens onto the region layers its solid crosses (:func:`~.geometry.slice_area`);
- every *active* ACU is a fixed-flow unit from its return solid to its supply solid at
  its supply temperature. A closed ACU (fault context) keeps its body in the ledger
  but has no port, aperture or temperature boundary in the equations;
- the 70 perforated blanking panels are porous openings cold -> hot layer
  (``sigma`` x sliced area, the manifest's porous parameter);
- region passages by family. OPEN: vertical mixing in each region, the cold
  region open to the space above the equipment, and two open aisle ends.
  CAC: the cold aisle's roof and end sheets, open only through their leak cutouts
  (exported sheet area subtracted from the gross sheet). HAC: the same sheets
  around the hot aisle; the exhausts and ACU returns see only the contained layers.

The raised-floor perimeter families (tile supply plenum, ceiling return plenum) are in
:mod:`.air_network_perimeter`; :data:`NETWORKS` / :func:`network_for` cover all six.
"""
from __future__ import annotations

import hashlib
import json
from collections import abc
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import torch

from agent.scenarios.air.l0_network import AirNetwork
from agent.scenarios.hybrid.compile import DependencyPending
from agent.scenarios.hybrid.fan_contract import FORMAT as CONTRACT_FORMAT
from agent.scenarios.hybrid.geometry import area_and_centre, slice_area
from agent.scenarios.hybrid.open_network_numerics import ImplicitState, solve_state
from agent.scenarios.hybrid.thermal_stream import UIDField, server_exhaust_temperature_by_uid

VERSION = "agent.hybrid.zonal-air-network/v1"
RATED_FLOW_M3S = 2.8
MIN_FLOW_FRACTION = .22
BLANKING_SPANS = ("_u1_u3", "_u8_u12", "_u21_u24", "_u29_u32", "_u41_u44")
BLANKING_FACES = 70


class ZonalOperator(AirNetwork):
    """The L0 air network with the full-state numerics of :mod:`~.open_network_numerics`."""

    def solve(self, controls, areas, *, tolerance=1e-10, max_iterations=60):
        if tolerance != 1e-10:
            raise ValueError("registered full residual tolerance")
        return solve_state(self, controls, areas)

    def differentiable_state(self, controls, areas):
        return ImplicitState.apply(controls, areas, self)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                          .encode()).hexdigest()


class ZonalAirNetwork:
    """One family's network on one fan contract (:mod:`~.fan_contract`) and its exported solids.

    ``solids`` is ``{solid name: (n, 3, 3) triangles}`` -- every solid the
    contract names (:func:`~.geometry.load_solids` reads them hash-checked).
    ``structural_fixture=True`` builds the operator from a contract without an
    accepted liquid run; such a network refuses to solve.
    """
    FAMILY = ""
    VERSION = VERSION

    def _bind(self, contract: Mapping[str, Any], solids: Mapping[str, Any], structural_fixture: bool):
        """Check the contract's identities and heat handoff; set the streams and the floor/top/ceiling heights."""
        c = contract
        self.contract = c
        if c["format"] != CONTRACT_FORMAT:
            raise ValueError("fan contract " + CONTRACT_FORMAT + " required")
        self.structural_fixture = bool(structural_fixture)
        if not c["accepted_liquid_identity_verified"] and not self.structural_fixture:
            raise ValueError("accepted same-source liquid handoff required")
        if self.structural_fixture and c["accepted_liquid_identity_verified"]:
            raise ValueError("structural fixtures must not impersonate paid liquid evidence")
        if c["operating_scenario"]["architecture"] != self.FAMILY:
            raise ValueError(f"{type(self).__name__} is the {self.FAMILY} network, not "
                             f"{c['operating_scenario']['architecture']}")
        self.solids = {name: np.asarray(tri, dtype=float) for name, tri in solids.items()}
        self.scenario = c["operating_scenario"]
        all_uids = tuple(sorted(self.scenario["all_acu_uids"]))
        self.acu_uids = tuple(a["uid"] for a in c["acus"])
        self.active_uids = tuple(u for u in self.acu_uids if u in self.scenario["active_acu_uids"])
        if set(self.acu_uids) != set(all_uids) or not self.active_uids or len(set(self.acu_uids)) != len(self.acu_uids):
            raise ValueError("complete equipment identity required")
        self.server_uids = tuple(s["uid"] for s in c["servers"])
        if len(self.server_uids) != 56 or len(set(self.server_uids)) != 56:
            raise ValueError("all original 56-server identities are required")
        if any(s["project_kind"] != "liquidServer" for s in c["servers"]):
            raise ValueError("liquidServer identity")
        self.server_flow = np.array([s["fixed_flow_m3s"] for s in c["servers"]], dtype=float)
        self.air_W = np.array([s["samples"][0]["Q_air_W"] for s in c["servers"]], dtype=float)
        if (not np.isfinite(self.server_flow).all() or not np.isfinite(self.air_W).all()
                or (self.server_flow <= 0).any() or (self.air_W <= 0).any()):
            raise ValueError("positive fixed fan streams and declared positive air heat required by this reference")
        for s in c["servers"]:
            t = s["samples"][0]
            if (abs(t["solid_storage_W"]) > 1e-6
                    or abs(t["Q_IT_W"] - t["Q_air_W"] - t["actual_Q_liquid_W"] - t["solid_storage_W"]) > 1e-6):
                raise ValueError("steady same-UID conservative heat handoff required")
        self.rho, self.cp = float(c["rho_kg_m3"]), float(c["cp_J_kgK"])
        floors = c["partitions"]["compiled_floor_definitions"]
        floor = float(floors["Raised_Floor"]["geometry"]["anchor"]["y"])
        ceiling = float(floors["Room_Ceiling"]["geometry"]["anchor"]["y"])
        rack_geometry = {s["rack_uid"]: s["rack_geometry"] for s in c["servers"]}
        top = max(g["location"]["y"] + g["size"]["y"] for g in rack_geometry.values())
        if not floor < top < ceiling:
            raise ValueError("actual floor/equipment/ceiling order")
        self.floor_y, self.top_y, self.ceiling_y = floor, top, ceiling
        return c

    def __init__(self, contract: Mapping[str, Any], solids: Mapping[str, Any], *, structural_fixture: bool = False):
        c = self._bind(contract, solids, structural_fixture)
        floor, top, ceiling = self.floor_y, self.top_y, self.ceiling_y
        floors = c["partitions"]["compiled_floor_definitions"]
        mid = (floor + top) / 2
        self.cold_bounds = (floor, mid, top)
        self.hot_bounds = self._hot_bounds(floor, mid, top, ceiling)
        room = floors["Raised_Floor"]["geometry"]["size"]
        self.room_area = float(room["W"] * room["D"])

        names = ["cold-lower", "cold-upper", "hot-lower", "hot-upper", "above-equipment"]
        height = [(floor + mid) / 2, (mid + top) / 2, (floor + mid) / 2, (mid + top) / 2, (top + ceiling) / 2]
        edges: List[Tuple[int, int]] = []
        areas: List[float] = []
        directed: List[bool] = []
        provenance: List[Dict[str, Any]] = []

        def link(a, b, area, oneway, record):
            if area <= 1e-12:
                return
            edges.append((a, b))
            areas.append(float(area))
            directed.append(oneway)
            provenance.append(record)

        def node(name, triangles):
            area, centre = area_and_centre(triangles)
            names.append(name)
            height.append(float(centre[1]))
            return len(names) - 1, area, centre

        def layers(bounds):
            return enumerate(zip(bounds[:-1], bounds[1:]))

        for a, b, area, record in self._region_links():
            link(a, b, area, False, record)
        porous = c["porous_blanking_sigma"]
        porous_count = 0
        for solid, tri in self.solids.items():
            if "_inlet_u" not in solid or not solid.endswith(BLANKING_SPANS):
                continue
            span = int(solid.rsplit("_u", 1)[1]) - int(solid.split("_u")[-2]) + 1
            sigma = float(porous["merged_perforatedBlankingPanel_" + str(span) + "U"])
            if not 0 < sigma <= 1:
                raise ValueError("actual porous area fraction")
            for level, (lo, hi) in layers(self.cold_bounds):
                link(level, 2 + level, slice_area(tri, lo, hi) * sigma, False,
                     {"kind": "source perforated blanking", "solid": solid, "sigma": sigma})
            porous_count += 1
        if porous_count != BLANKING_FACES:
            raise ValueError("fixed actual fourteen-rack blanking faces")
        servers, units, metadata = [], [], []
        for s in c["servers"]:
            intake, exhaust = self.solids[s["intake"]["original_solid"]], self.solids[s["exhaust"]["original_solid"]]
            a, area_in, _ = node(s["uid"] + "/intake", intake)
            b, area_out, _ = node(s["uid"] + "/exhaust", exhaust)
            servers.append((a, b))
            if abs(area_in - s["intake"]["area_m2"]) > 1e-6 or abs(area_out - s["exhaust"]["area_m2"]) > 1e-6:
                raise ValueError("source port area mismatch")
            for level, (lo, hi) in layers(self.cold_bounds):
                link(level, a, slice_area(intake, lo, hi), True, {"kind": "true intake", "uid": s["uid"]})
            for level, (lo, hi) in layers(self.hot_bounds):
                link(b, 2 + level, slice_area(exhaust, lo, hi), True, {"kind": "true exhaust", "uid": s["uid"]})
        nominal_t, nominal_q = [], []
        for acu in c["acus"]:
            uid, controls = acu["uid"], acu["effective_controls"]
            active = uid in self.active_uids
            metadata.append({"uid": uid, "active": active, "body_geometry_preserved": True,
                             "port_states_in_equations": active})
            if not active:
                if (controls["compiled_supply_flow_m3s"] != 0 or controls["compiled_return_requested_flow_m3s"] != 0
                        or not acu["closed_ports"]):
                    raise ValueError("failed branch must be strictly closed")
                continue
            if controls["compiled_supply_flow_m3s"] <= 0:
                raise ValueError("active flow must be positive")
            bindings = acu["source_bound_BC_bindings"]
            supply = self.solids[next(x["solidName"] for x in bindings if x["patchName"] == controls["supply_patch_name"])]
            ret = self.solids[next(x["solidName"] for x in bindings if x["patchName"] == controls["return_patch_name"])]
            si, _, _ = node(uid + "/supply", supply)
            ri, _, _ = node(uid + "/return", ret)
            units.append((ri, si))
            for level, (lo, hi) in layers(self.cold_bounds):
                link(si, level, slice_area(supply, lo, hi), True, {"kind": "active ACU supply", "uid": uid})
            for level, (lo, hi) in layers(self.hot_bounds):
                link(2 + level, ri, slice_area(ret, lo, hi), True, {"kind": "active ACU return", "uid": uid})
            nominal_t.append(controls["compiled_supply_temperature_C"])
            nominal_q.append(controls["compiled_supply_flow_m3s"])
        # Pressure is identifiable only through passive passages; never add a fake leak.
        reached = {0}
        while True:
            expanded = reached | {j for i, j in edges if i in reached} | {i for i, j in edges if j in reached}
            if expanded == reached:
                break
            reached = expanded
        if len(reached) != len(names):
            raise ValueError("active passive-pressure graph disconnected")
        self.network = ZonalOperator(tuple(names), tuple(height), tuple(edges), tuple(directed), tuple(servers),
                                     tuple(self.server_flow), tuple(self.air_W / 1000), tuple(units),
                                     (1.,) * len(units), rho=self.rho, cp=self.cp)
        self.areas = torch.tensor(areas, dtype=torch.float64)
        self.nominal_controls = torch.tensor(nominal_t + nominal_q, dtype=torch.float64)
        self.identity = {"version": self.VERSION, "family": self.FAMILY, "contract_sha256": _digest(c),
                         "all_ACU_UIDs": self.acu_uids, "active_ACU_UIDs": self.active_uids,
                         "server_UIDs": self.server_uids, "nodes": len(names),
                         "state_size": self.network.state_size, "passages": len(edges),
                         "unit_geometry_and_mask": metadata, "region_geometry": self.region_geometry,
                         "passage_provenance": provenance,
                         "inactive_semantics": "same body/ports retained externally; exact closed-port elimination "
                                               "from state equations",
                         "fidelity": "unfitted " + self.FAMILY + " zonal pressure/enthalpy plus original per-UID 1D "
                                     "thermal streams",
                         "structural_fixture_not_physical_handoff": self.structural_fixture,
                         "containment_geometry": c["containment_geometry"],
                         "source_sheet_open_areas_m2": getattr(self, "sheet_open_areas_m2", {}),
                         "I2_HF_available": False,
                         "subfloor_flow": "not represented; in-row supply, not a plenum-supply circuit"}

    # -- family hooks ---------------------------------------------------------------------------
    def _hot_bounds(self, floor, mid, top, ceiling):
        return floor, mid, top, ceiling

    def _region_links(self):
        raise NotImplementedError

    def _sheets(self, prefix: str, kind: str, minimum_width: float):
        """The contained aisle's footprint and its three exported sheets, each with its open (leak) area."""
        c = self.contract
        if set(c["partitions"]["baffles"]) != {prefix + "end_left", prefix + "end_right", prefix + "roof"}:
            raise ValueError("actual three-sheet " + self.FAMILY + " source inventory required")
        containment = c["containment_geometry"]
        if containment["kind"] != kind:
            raise ValueError(self.FAMILY + " is " + kind + " containment")
        if set(c["partitions"]["compiled_baffle_definitions"]) != set(c["partitions"]["baffles"]):
            raise ValueError("actual sheet export/source inventory differs")
        xmin, xmax, zmin, zmax = containment["aisle_floor_rectangle_m"]
        width, length = zmax - zmin, xmax - xmin
        if width < minimum_width - 1e-8:
            raise ValueError("registered " + self.FAMILY + " aisle geometry")
        if abs(containment["roof_y_m"] - self.top_y) > 1e-9 or abs(containment["floor_y_m"] - self.floor_y) > 1e-9:
            raise ValueError(self.FAMILY + " sheet floor/top must meet actual equipment boundaries")
        sheets = {}
        for sheet in containment["sheets"]:
            triangles = self.solids.get(sheet["uid"])
            if triangles is None:
                raise ValueError(self.FAMILY + " sheet absent from actual grouped STL")
            area, _ = area_and_centre(triangles)
            if abs(area - sheet["one_sided_STL_material_area_m2_expected"]) > 2e-5:
                raise ValueError("actual " + self.FAMILY + " triangles do not preserve the declared leak cutout: "
                                 + sheet["uid"])
            sheets[sheet["uid"]] = {"tri": triangles, "actual_area_m2": area,
                                    "open_area_m2": sheet["gross_area_m2"] - area}
        self.sheet_open_areas_m2 = {u: v["open_area_m2"] for u, v in sheets.items()}
        return width, length, sheets

    def _end_cutouts(self, prefix, width, sheets):
        links = []
        for uid in (prefix + "end_left", prefix + "end_right"):
            sheet = sheets[uid]
            for level, (lo, hi) in enumerate(zip(self.cold_bounds[:-1], self.cold_bounds[1:])):
                material = slice_area(sheet["tri"], lo, hi)
                gross = width * (hi - lo)
                links.append((level, 2 + level, max(0., gross - material),
                              {"kind": "source " + self.FAMILY + " end open cutout", "source_uid": uid,
                               "gross_slice_area_m2": gross, "actual_material_slice_area_m2": material,
                               "height_interval_m": [lo, hi]}))
        return links

    # -- solve --------------------------------------------------------------------------------
    def fields(self, controls: Optional[torch.Tensor] = None, *, differentiable: bool = False) -> Dict[str, Any]:
        """The solved state and its fields at ``controls`` (active supply temperatures, then active flows)."""
        if self.structural_fixture:
            raise RuntimeError("unpaid structural assembly cannot solve or emit physical fields")
        controls = self.nominal_controls.clone() if controls is None else controls
        n = len(self.active_uids)
        if (controls.shape != (2 * n,) or controls.dtype != torch.float64 or controls.device.type != "cpu"
                or not torch.isfinite(controls).all()):
            raise ValueError("active control vector shape/dtype")
        if not bool(((controls[n:] >= MIN_FLOW_FRACTION * RATED_FLOW_M3S) & (controls[n:] <= RATED_FLOW_M3S)).all()):
            raise ValueError("active catalog flow range")
        if differentiable:
            state, receipt = self.network.differentiable_state(controls, self.areas), None
        else:
            state, receipt = self.network.solve(controls, self.areas)
        data = self.network.temperatures(state, controls, self.areas)
        uids = self.server_uids
        reference = server_exhaust_temperature_by_uid(
            uids, UIDField(uids, data["server_inlet_c"] + 273.15), UIDField(uids, state.new_tensor(self.air_W)),
            UIDField(uids, state.new_tensor(self.server_flow)), rho_kg_m3=self.rho, cp_J_kgK=self.cp).values - 273.15
        index = {u: i for i, u in enumerate(self.active_uids)}
        return {**data, "state": state, "solve_receipt": receipt, "ACU_UID_order": self.acu_uids,
                "active_ACU_UID_order": self.active_uids,
                "ACU_return_c_by_uid": {u: data["unit_return_c"][index[u]] if u in index else None
                                        for u in self.acu_uids},
                "ACU_flow_by_uid": {u: controls[n + index[u]] if u in index else controls.new_zeros(())
                                    for u in self.acu_uids},
                "ACU_supply_c_by_uid": {u: controls[index[u]] if u in index else None for u in self.acu_uids},
                "server_1D_reference_exhaust_c": reference, "server_fixed_flow_m3s": state.new_tensor(self.server_flow),
                "server_air_heat_W": state.new_tensor(self.air_W), "physics_call_count": 1}

    def steady_fields(self) -> Dict[str, Any]:
        """:meth:`fields` at the contract's controls, plus the legacy residual at the root."""
        fields = self.fields()
        fields["original_full_residual_inf"] = float(
            self.network.residual(fields["state"], self.nominal_controls, self.areas).abs().max())
        return fields


class OpenAirNetwork(ZonalAirNetwork):
    """SLAB_INROW_OPEN: no containment; two opposed in-row banks around one cold aisle."""
    FAMILY = "SLAB_INROW_OPEN"

    def _region_links(self):
        c = self.contract
        if c["partitions"]["baffles"] or c["partitions"]["compiled_baffle_definitions"]:
            raise ValueError("OPEN requires physical removal, not viewport hiding")
        inlet_z = [float(area_and_centre(self.solids[s["intake"]["original_solid"]])[1][2]) for s in c["servers"]]
        width = max(inlet_z) - min(inlet_z)
        # This reference is exactly two opposed, parallel in-row banks.
        if width < 1.22 - 1e-8:
            raise ValueError("registered cold aisle geometry not present")
        xs = []
        for g in [s["rack_geometry"] for s in c["servers"]] + [a["geometry"] for a in c["acus"]]:
            q, location, size = g["quaternion"], g["location"], g["size"]
            if abs(q["x"]) + abs(q["z"]) > 1e-10 or not (abs(abs(q["w"]) - 1) < 1e-10 or abs(abs(q["y"]) - 1) < 1e-10):
                raise ValueError("current OPEN reference requires opposed cardinal rows")
            direction = 1 if abs(q["w"]) > .5 else -1
            xs.extend([location["x"], location["x"] + direction * size["x"]])
        length = max(xs) - min(xs)
        cold, hot = width * length, self.room_area - width * length
        if min(cold, hot) <= 0:
            raise ValueError("positive actual region area")
        self.region_geometry = {"cold_width_m": width, "row_length_m": length, "cold_area_m2": cold,
                                "hot_area_m2": hot, "floor_y_m": self.floor_y, "equipment_top_y_m": self.top_y,
                                "ceiling_y_m": self.ceiling_y}
        links = [(0, 1, cold, {"kind": "OPEN virtual vertical mixing", "area_m2": cold}),
                 (2, 3, hot, {"kind": "OPEN virtual vertical mixing", "area_m2": hot}),
                 (1, 4, cold, {"kind": "OPEN roof aperture, no containment roof", "area_m2": cold}),
                 (3, 4, hot, {"kind": "OPEN above-equipment communication", "area_m2": hot})]
        for level, (lo, hi) in enumerate(zip(self.cold_bounds[:-1], self.cold_bounds[1:])):
            links.append((level, 2 + level, 2 * width * (hi - lo),
                          {"kind": "two explicit open aisle-end passages", "width_m": width,
                           "height_interval_m": [lo, hi], "count": 2}))
        return links


class ColdContainedAirNetwork(ZonalAirNetwork):
    """SLAB_INROW_CAC: the cold aisle closed by two end sheets and a roof, each with an open leak cutout."""
    FAMILY = "SLAB_INROW_CAC"

    def _region_links(self):
        prefix = "ispo_cac_"
        width, length, sheets = self._sheets(prefix, "cold", 1.22)
        cold, hot = width * length, self.room_area - width * length
        if min(cold, hot) <= 0:
            raise ValueError("positive actual region area")
        self.region_geometry = {"cold_width_m": width, "row_length_m": length, "cold_area_m2": cold,
                                "hot_area_m2": hot, "floor_y_m": self.floor_y, "equipment_top_y_m": self.top_y,
                                "ceiling_y_m": self.ceiling_y}
        roof = sheets[prefix + "roof"]
        return [(0, 1, cold, {"kind": "CAC virtual vertical mixing", "area_m2": cold}),
                (2, 3, hot, {"kind": "CAC virtual vertical mixing", "area_m2": hot}),
                (1, 4, roof["open_area_m2"], {"kind": "source CAC roof open cutout", "source_uid": prefix + "roof",
                                              "gross_area_m2": cold, "actual_material_area_m2": roof["actual_area_m2"]}),
                (3, 4, hot, {"kind": "outside-CAC above-equipment communication", "area_m2": hot}),
                *self._end_cutouts(prefix, width, sheets)]


class HotContainedAirNetwork(ZonalAirNetwork):
    """SLAB_INROW_HAC: the hot aisle contained; exhausts and ACU returns open only onto its two layers."""
    FAMILY = "SLAB_INROW_HAC"

    def _hot_bounds(self, floor, mid, top, ceiling):
        return floor, mid, top

    def _region_links(self):
        prefix = "ispo_hac_"
        width, length, sheets = self._sheets(prefix, "hot", .91)
        hot, cold = width * length, self.room_area - width * length
        if min(cold, hot) <= 0:
            raise ValueError("positive actual region area")
        self.region_geometry = {"contained_hot_width_m": width, "row_length_m": length, "cold_area_m2": cold,
                                "hot_area_m2": hot, "floor_y_m": self.floor_y, "equipment_top_y_m": self.top_y,
                                "ceiling_y_m": self.ceiling_y}
        roof = sheets[prefix + "roof"]
        return [(0, 1, cold, {"kind": "HAC virtual vertical mixing", "area_m2": cold}),
                (2, 3, hot, {"kind": "HAC virtual vertical mixing", "area_m2": hot}),
                (3, 4, roof["open_area_m2"], {"kind": "source HAC roof open cutout", "source_uid": prefix + "roof",
                                              "gross_area_m2": hot, "actual_material_area_m2": roof["actual_area_m2"]}),
                (1, 4, cold, {"kind": "outside-HAC cold-above-equipment communication", "area_m2": cold}),
                *self._end_cutouts(prefix, width, sheets)]


class _Registry(abc.Mapping):
    """``{family: network class}`` of every family with a network; the perimeter module is imported on first use."""

    def _table(self):
        from agent.scenarios.hybrid.air_network_perimeter import PERIMETER_NETWORKS
        return {cls.FAMILY: cls for cls in (OpenAirNetwork, ColdContainedAirNetwork, HotContainedAirNetwork,
                                            *PERIMETER_NETWORKS)}

    def __getitem__(self, family):
        return self._table()[family]

    def __iter__(self):
        return iter(self._table())

    def __len__(self):
        return len(self._table())


NETWORKS: Mapping[str, type] = _Registry()


def network_for(architecture: str) -> type:
    """The network class of a registered family; ``DependencyPending`` for one without a network yet."""
    if architecture in NETWORKS:
        return NETWORKS[architecture]
    raise DependencyPending([f"{architecture} zonal LF air network"])
