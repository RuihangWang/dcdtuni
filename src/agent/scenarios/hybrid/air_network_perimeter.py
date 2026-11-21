"""Zonal LF air networks of the raised-floor perimeter Shinaide families (RF_PERIM_OPEN, _CAC, _HAC).

Ports ``agent_legacy/scenario_hybrid/air_network_perimeter_v1.py`` (network
``shinaide-perimeter-tile-supply-return-plena-network/v1``, the one the legacy
perimeter-21 LF runs of 2026-10-11 used). The passages, their order and every
area are the legacy's, so the operator and its root are the same; the legacy
residual stays the acceptance test (full residual <= 1e-10).

Beyond the five region nodes of the slab families (:mod:`.air_network`) the
perimeter families have plena:

- a **supply plenum** under the raised floor: every active perimeter ACU blows down
  into it through its exported bottom (supply) solid, and it feeds the cold region's
  lower layer through each perforated floor tile (``sigma`` x the tile's exported
  area, both bound to the manifest's floor openings and porous bindings);
- OPEN: the cold and hot regions open to the space above the equipment and to each
  other through two open aisle ends; the ACU returns draw from the hot layers;
- CAC: the cold aisle's roof and end sheets, open only through their leak cutouts;
  the ACU returns draw from the hot layers;
- HAC: a **ceiling return plenum** above a sealed false ceiling: the contained hot
  aisle's perforated roof opens into it (roof ``sigma``), and every active ACU returns
  from it through its sealed duct; the cold room is closed by the false ceiling.

The tile/roof ``sigma``, the tile size and the false-ceiling height are the task's
registered synthetic parameters (``containment_geometry.synthetic_parameters``, the
v5 task's ``tiles_plena``); the network checks that the exported triangles and
porous bindings carry them. A closed ACU keeps its body but has no port.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

import numpy as np
import torch

from agent.scenarios.hybrid.air_network import (BLANKING_FACES, BLANKING_SPANS, ZonalAirNetwork, ZonalOperator,
                                                _digest)
from agent.scenarios.hybrid.geometry import area_and_centre, slice_area

VERSION = "agent.hybrid.perimeter-plena-air-network/v1"
LEGACY_VERSION = "shinaide-perimeter-tile-supply-return-plena-network/v1"
POSITION_TOLERANCE_M = 2e-5   # the legacy's tile centre / area tolerances
AREA_TOLERANCE_M2 = 2e-5


class PerimeterAirNetwork(ZonalAirNetwork):
    """One RF_PERIM family's network on one fan contract and its exported solids (see the module)."""
    FAMILY = ""
    KIND = ""          # containment kind of the family: "none", "cold" or "hot"
    VERSION = VERSION

    def __init__(self, contract: Mapping[str, Any], solids: Mapping[str, Any], *, structural_fixture: bool = False):
        c = self._bind(contract, solids, structural_fixture)
        floor, top, ceiling = self.floor_y, self.top_y, self.ceiling_y
        geo = c["containment_geometry"]
        if geo["kind"] != self.KIND:
            raise ValueError(f"{self.FAMILY} is {self.KIND} containment, not {geo['kind']}")
        params = geo["synthetic_parameters"]
        hot, contained = self.KIND == "hot", self.KIND in ("cold", "hot")
        mid = (floor + top) / 2
        self.cold_bounds = (floor, mid, top)
        self.hot_bounds = (floor, mid, top) if hot else (floor, mid, top, ceiling)
        floors = c["partitions"]["compiled_floor_definitions"]
        xmin, xmax, zmin, zmax = geo["contained_rectangle_XZ_m"]
        width, length = xmax - xmin, zmax - zmin
        room = floors["Raised_Floor"]["geometry"]["size"]
        room_area = float(room["W"] * room["D"])
        central = width * length
        cold_area = room_area - central if hot else central
        hot_area = central if hot else room_area - central
        if min(cold_area, hot_area) <= 0:
            raise ValueError("positive real central/outside regions")
        false_ceiling = float(params["false_ceiling_y_m"]) if hot else None
        if hot and not top < false_ceiling < ceiling:
            raise ValueError("the false ceiling must lie between the equipment top and the room ceiling")
        upper = false_ceiling if hot else ceiling
        names = ["cold-lower", "cold-upper", "hot-lower", "hot-upper", "outside-above-equipment", "supply-plenum"]
        height = [(floor + mid) / 2, (mid + top) / 2, (floor + mid) / 2, (mid + top) / 2, (top + upper) / 2, floor / 2]
        if hot:
            names.append("ceiling-return-plenum")
            height.append((false_ceiling + ceiling) / 2)
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

        porous = c["porous_blanking_sigma"]
        patches = c["porous_baffle_patches"]

        def porous_sigma(surface, expected, what):
            sigma = porous.get(surface)
            if sigma is None or len(patches.get(surface, ())) != 2 or abs(sigma - expected) > 1e-12:
                raise ValueError(what + ": the export's porous sigma / master-slave pair is not the registered one")
            return float(sigma)

        link(0, 1, cold_area, False, {"kind": "source-region cold vertical mixing", "area_m2": cold_area})
        link(2, 3, hot_area, False, {"kind": "source-region hot vertical mixing", "area_m2": hot_area})
        tile_size = params["tile_size_m"]
        tile_area = float(tile_size[0] * tile_size[1])
        openings = floors["Raised_Floor"]["openings"]
        if len(geo["tiles"]) != params["tile_count"]:
            raise ValueError("the registered tile count is not the action's tile inventory")
        witness = []
        for tile in geo["tiles"]:
            opening = next(o for o in openings if o["patchId"] == tile["uid"])
            solid = opening["solidName"]
            area, centre = area_and_centre(self.solids[solid])
            if max(abs(float(centre[i]) - tile["center_xyz_m"][i]) for i in range(3)) > POSITION_TOLERANCE_M:
                raise ValueError("true tile world centre differs from the registered layout: " + tile["uid"])
            if abs(area - tile_area) > AREA_TOLERANCE_M2:
                raise ValueError("exported tile triangles differ from the registered tile size: " + tile["uid"])
            sigma = porous_sigma(solid, params["sigma"], "tile " + tile["uid"])
            link(5, 0, area * sigma, False, {"kind": "actual source tile supply from plenum", "source_tile": tile["uid"],
                                             "actual_area_m2": area, "sigma": sigma})
            witness.append({"uid": tile["uid"], "solid": solid, "actual_area_m2": area, "sigma": sigma,
                            "center_xyz_m": centre.tolist(), "porous_patch_names": list(patches[solid])})
        if not contained:
            link(1, 4, cold_area, False, {"kind": "actual OPEN above cold region", "area_m2": cold_area})
            link(3, 4, hot_area, False, {"kind": "actual OPEN above hot region", "area_m2": hot_area})
            for level, (lo, hi) in layers(self.cold_bounds):
                link(level, 2 + level, 2 * width * (hi - lo), False,
                     {"kind": "actual two open aisle ends", "gross_area_m2": 2 * width * (hi - lo)})
        else:
            prefix = "rf_hac_" if hot else "rf_cac_"
            if {s["uid"] for s in geo["sheets"]} != {prefix + "end_low", prefix + "end_high", prefix + "roof"}:
                raise ValueError("actual three-sheet " + self.FAMILY + " inventory required")
            roof_area, _ = area_and_centre(self.solids[prefix + "roof"])
            if hot:
                if abs(roof_area - central) > AREA_TOLERANCE_M2:
                    raise ValueError("actual HAC perforated roof area")
                sigma = porous_sigma(prefix + "roof", params["HAC_roof_sigma"], "HAC roof")
                link(3, 6, roof_area * sigma, False, {"kind": "actual perforated HAC roof into ceiling return plenum",
                                                      "sigma": sigma, "source_solid": prefix + "roof"})
                link(1, 4, cold_area, False, {"kind": "cold room below sealed false ceiling",
                                              "gross_area_m2": cold_area})
            else:
                link(1, 4, central - roof_area, False, {"kind": "actual CAC roof open cutout",
                                                        "source_solid": prefix + "roof"})
                link(3, 4, hot_area, False, {"kind": "outside CAC above communication", "gross_area_m2": hot_area})
            for uid in (prefix + "end_low", prefix + "end_high"):
                triangles = self.solids[uid]
                for level, (lo, hi) in layers(self.cold_bounds):
                    aperture = max(0., width * (hi - lo) - slice_area(triangles, lo, hi))
                    link(level, 2 + level, aperture, False,
                         {"kind": "actual contained end open cutout", "source_solid": uid})
        count = 0
        for solid, triangles in self.solids.items():
            if "_inlet_u" not in solid or not solid.endswith(BLANKING_SPANS):
                continue
            span = int(solid.rsplit("_u", 1)[1]) - int(solid.split("_u")[-2]) + 1
            sigma = float(porous["merged_perforatedBlankingPanel_" + str(span) + "U"])
            if not 0 < sigma <= 1:
                raise ValueError("actual porous area fraction")
            for level, (lo, hi) in layers(self.cold_bounds):
                link(level, 2 + level, slice_area(triangles, lo, hi) * sigma, False,
                     {"kind": "source perforated blanking", "solid": solid, "sigma": sigma})
            count += 1
        if count != BLANKING_FACES:
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
            supply_solid = next(x["solidName"] for x in bindings if x["patchName"] == controls["supply_patch_name"])
            return_solid = next(x["solidName"] for x in bindings if x["patchName"] == controls["return_patch_name"])
            si, area_s, _ = node(uid + "/supply", self.solids[supply_solid])
            ri, area_r, _ = node(uid + "/return", self.solids[return_solid])
            units.append((ri, si))
            link(si, 5, area_s, True, {"kind": "true downward ACU supply into underfloor plenum", "uid": uid,
                                       "source_solid": supply_solid})
            if hot:
                link(6, ri, area_r, True, {"kind": "true ACU return through its sealed duct and the ceiling plenum",
                                           "uid": uid, "source_solid": return_solid})
            else:
                for level, (lo, hi) in layers(self.hot_bounds):
                    link(2 + level, ri, slice_area(self.solids[return_solid], lo, hi), True,
                         {"kind": "true room-side ACU return", "uid": uid})
            nominal_t.append(controls["compiled_supply_temperature_C"])
            nominal_q.append(controls["compiled_supply_flow_m3s"])
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
        self.region_geometry = {"cold_width_m": width, "row_length_m": length, "cold_area_m2": cold_area,
                                "hot_area_m2": hot_area, "floor_y_m": floor, "equipment_top_y_m": top,
                                "ceiling_y_m": ceiling}
        self.identity = {"version": self.VERSION, "legacy_version": LEGACY_VERSION, "family": self.FAMILY,
                         "contract_sha256": _digest(c), "all_ACU_UIDs": self.acu_uids,
                         "active_ACU_UIDs": self.active_uids, "server_UIDs": self.server_uids, "nodes": len(names),
                         "state_size": self.network.state_size, "passages": len(edges),
                         "unit_geometry_and_mask": metadata, "region_geometry": self.region_geometry,
                         "passage_provenance": provenance,
                         "inactive_semantics": "same body/ports retained externally; exact closed-port elimination "
                                               "from state equations",
                         "fidelity": "unfitted " + self.FAMILY + " zonal pressure/enthalpy with supply"
                                     + (" and ceiling return" if hot else "") + " plena, plus original per-UID 1D "
                                     "thermal streams",
                         "structural_fixture_not_physical_handoff": self.structural_fixture,
                         "I2_HF_available": False,
                         "subfloor_flow": "supply plenum fed by the true downward ports through the perforated tiles",
                         "tile_boundary_witness": witness, "perimeter_geometry": geo,
                         "supply_plenum_gross_volume_m3": room_area * floor,
                         "return_plenum_gross_volume_m3": room_area * (ceiling - false_ceiling) if hot else 0.}


class PerimeterOpenAirNetwork(PerimeterAirNetwork):
    """RF_PERIM_OPEN: downflow perimeter units, tile supply, no containment."""
    FAMILY, KIND = "RF_PERIM_OPEN", "none"


class PerimeterColdContainedAirNetwork(PerimeterAirNetwork):
    """RF_PERIM_CAC: tile supply into a contained cold aisle with leak cutouts; room-side returns."""
    FAMILY, KIND = "RF_PERIM_CAC", "cold"


class PerimeterHotContainedAirNetwork(PerimeterAirNetwork):
    """RF_PERIM_HAC: tile supply; a perforated hot-aisle roof into a ceiling return plenum; ducted returns."""
    FAMILY, KIND = "RF_PERIM_HAC", "hot"


PERIMETER_NETWORKS = (PerimeterOpenAirNetwork, PerimeterColdContainedAirNetwork, PerimeterHotContainedAirNetwork)
