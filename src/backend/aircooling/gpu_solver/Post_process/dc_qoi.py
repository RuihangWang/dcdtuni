"""Data-centre engineering quantities of a solved state, as differentiable torch functions.

These are the quantities the dctwin HF reward reads (``backend.dcdesign.hf`` via ``api/air_metrics.py``):

* per ACU: the return flux-mean temperature ``sum(phi T) / sum(phi)`` over the return patch and the heat it
  removes ``rho c_p Q_supply (T_return - T_supply)`` (the HF reward's definition: it reproduces the four
  ``heatRemovedW`` of the INROW_OPEN reference to < 1 W from their own flux-mean temperatures);
* per server: the area-mean temperature of its intake (front) patch;
* the total heat removed by the ACUs (the air-side energy closure against the declared IT heat).

Boundary values follow the case's conditions as translated by ``Parse_mesh/dctwin_bc.py``: on returns and
server intakes T is zeroGradient (the face value is the owner cell's); the face flux is the prescribed uniform
suction ``Q/A`` on pinned returns and intakes (``rackInletVelocity``), ``U_owner . S_f`` on the reference
return (zeroGradient U), and on a pinned return translated with ``fan_inlet="openfoam"`` (``bc/U.toml``
``flowRateOutletVelocity``) the condition's own face velocity: the owner velocity's normal part clamped >= 0 and
rescaled (or shifted) to the flow rate, exactly as ``FVdomain.SetBC.onthefly.setU.Flowrateoutletvelocity`` writes
the ghost (``flow_rate_outlet_flux``). Temperatures are returned in kelvin (the state carries the excess over
``TRef``).

    q = DCQuantities(case_dir, device="cuda")          # case_dir holds the .h5, patchinfo.toml, dctwin_map.json
    out = q(U, T)                                        # U [N, 3], T [N] (excess); dict of tensors
    vec = q.vector(out)                                  # fixed order, q.names
"""

from __future__ import annotations

import glob
import json
import os
try:
    import tomllib
except ModuleNotFoundError:                 # Python 3.10
    import tomli as tomllib

import h5py
import numpy as np
import torch

__all__ = ["DCQuantities", "flow_rate_outlet_flux"]


def _scalar(spec) -> float:
    return float(spec["val"] if isinstance(spec, dict) else spec)


def flow_rate_outlet_flux(U_owner: torch.Tensor, normal: torch.Tensor, area: torch.Tensor, target: float,
                          rho: float = 1.0) -> torch.Tensor:
    """``(u_f . n) A`` of ``flowRateOutletVelocity``'s face velocity on one patch (``setU.Flowrateoutletvelocity``):
    ``u_f = u_tan + n_new n`` with ``u_tan`` the owner velocity's tangential part and ``n_new`` its normal part
    clamped >= 0 and rescaled by ``|Q / (Q_est + 1e-12)|``, or shifted by ``(Q - Q_est) / (sum(rho A) + 1e-12)`` when
    the estimate ``Q_est = sum(rho A max(U . n, 0))`` is at most ``Q / 2``.  ``U_owner`` [F, 3], ``normal`` [F, 3]
    (unit), ``area`` [F]."""
    a = area.reshape(-1, 1)
    nup = (U_owner * normal).sum(-1, keepdim=True)
    tan = U_owner - nup * normal
    nup = torch.clamp(nup, min=0.0)
    est = torch.sum(rho * a * nup, dim=-2, keepdim=True)
    a_sum = torch.sum(rho * a, dim=-2, keepdim=True)
    scale = torch.abs(target / (est + 1e-12))
    adjustment = (target - est) / (a_sum + 1e-12)
    nup = torch.where(est > 0.5 * target, nup * scale, nup + adjustment)
    u_face = tan + nup * normal
    return (u_face * normal).sum(-1) * area.reshape(-1)


class DCQuantities:
    def __init__(self, case_dir: str, device="cpu", dtype=torch.float64, rho: float = 1.19, cp: float = 1005.0):
        h5s = sorted(glob.glob(os.path.join(case_dir, "*.h5")))
        if len(h5s) != 1:
            raise FileNotFoundError(f"{case_dir}: expected one .h5, found {h5s}")
        self.map = json.load(open(os.path.join(case_dir, "dctwin_map.json")))
        patches = tomllib.load(open(os.path.join(case_dir, "patchinfo.toml"), "rb"))["patch"]
        span = {p["patch_name"]: (int(p["start_face"]), int(p["n_faces"])) for p in patches.values()}
        with h5py.File(h5s[0], "r") as f:
            area = np.asarray(f["face|face_area"]).reshape(-1)
            normal = np.asarray(f["face|face_normal"])
            owner = np.asarray(f["cpd|neighbor_cell"][0])
        self.TRef = float(self.map["TRef"])
        self.span = span
        self.rho, self.cp = rho, cp
        self.device, self.dtype = torch.device(device), dtype
        self.face_area_np = area                         # host copy (every face), for checks on a patch's faces

        def faces(patch):
            s, n = span[patch]
            sl = slice(s, s + n)
            t = lambda a, dt=dtype: torch.as_tensor(np.ascontiguousarray(a[sl]), dtype=dt, device=self.device)
            return dict(owner=t(owner, torch.long), area=t(area), normal=t(normal), span=(s, n))

        u_bc = {}
        u_toml = os.path.join(case_dir, "bc", "U.toml")
        if os.path.isfile(u_toml):
            u_bc = tomllib.load(open(u_toml, "rb")).get("boundaryfield", {})
        self.acus = {}
        for name, a in sorted(self.map["acus"].items()):
            sup, ret = a["supply"], a["return"]
            self.acus[name] = dict(ret=faces(ret["patch"]), pinned=bool(ret.get("pinned")),
                                   Q=float(sup["Q"]), T_sup=float(sup["T_supply_K"]))
            entry = u_bc.get(ret["patch"], {})
            if ret.get("pinned") and entry.get("type") == "flowRateOutletVelocity":
                # (target flow, rho of the estimate): volumetric, or a mass flow over rho (setU's two branches)
                if entry.get("volumetricFlowRate") is not None:
                    self.acus[name]["frov"] = (_scalar(entry["volumetricFlowRate"]), 1.0)
                else:
                    self.acus[name]["frov"] = (_scalar(entry["massFlowRate"]), float(entry.get("rho", 1.0)))
        self.servers = {}
        for out_patch, s in sorted(self.map["servers"].items()):
            if s["intake"] in span and span[s["intake"]][1] > 0:
                self.servers[s["intake"]] = faces(s["intake"])
        self.names = ([f"return_T:{a}" for a in self.acus] + [f"heat_W:{a}" for a in self.acus]
                      + [f"inlet_T:{s}" for s in self.servers] + ["heat_W:total"])
        self._srv = None

    def compact_cells(self) -> torch.Tensor:
        """Renumber the cells the quantities read (the owner cells of the return and intake faces) to 0 .. K-1 and
        return their K original ids, sorted: ``__call__`` then takes U [K, 3] and T [K] of those cells only (the
        partitioned driver gathers them instead of whole fields)."""
        tabs = [a["ret"] for a in self.acus.values()] + list(self.servers.values())
        need = torch.unique(torch.cat([t["owner"].reshape(-1) for t in tabs]))
        for t in tabs:
            t["owner"] = torch.searchsorted(need, t["owner"])
        self._srv = None
        return need

    def _server_tables(self) -> dict:
        """The intake faces of all servers in one table (owner, area, server index), built on first use."""
        if self._srv is None:
            tabs = list(self.servers.values())
            seg = torch.cat([torch.full((t["owner"].numel(),), i, dtype=torch.long, device=self.device)
                             for i, t in enumerate(tabs)])
            area = torch.cat([t["area"] for t in tabs])
            self._srv = dict(owner=torch.cat([t["owner"] for t in tabs]), area=area, seg=seg, n=len(tabs),
                             asum=torch.zeros(len(tabs), dtype=area.dtype, device=self.device).index_add_(0, seg, area))
        return self._srv

    def supply_enthalpy(self) -> float:
        """rho cp sum(Q (T_supply - TRef)) over the units: what the supplies carry in, relative to TRef (W)."""
        return self.rho * self.cp * sum(a["Q"] * (a["T_sup"] - self.TRef) for a in self.acus.values())

    def __call__(self, U: torch.Tensor, T: torch.Tensor, un: torch.Tensor | None = None,
                 return_enthalpy=None) -> dict[str, torch.Tensor]:
        """``un``: the solver's own face normal velocity (``face_volumetric_flux``, every face, the dataset's face
        order) of this state.  With it the return flows are the discrete ones and ``heat_W:total`` is the enthalpy
        flow through the units' patches, rho cp (sum_returns phi (T - TRef) - sum_supplies Q (T_sup - TRef)): the
        quantity dctwin's convergence rule checks (its ``heatT_`` / ``phi_`` monitors).  Without it the total is
        the sum over units of supply flow times the rise of the return temperature, which equals the former only
        where every unit returns exactly what it supplies and the weights are the discrete fluxes
        (``heat_W:supply_flow`` keeps that number either way).  ``return_enthalpy``: the returns' sum, in W, when
        the caller has reduced it over the parts of a partitioned run."""
        out = {}
        total = 0.0
        h_ret = 0.0
        for name, a in self.acus.items():
            r = a["ret"]
            Tf = T[r["owner"]] + self.TRef
            if un is not None:
                s_, n_ = r["span"]
                phi = un[s_:s_ + n_].reshape(-1).to(Tf.dtype) * r["area"]
                h_ret = h_ret + self.rho * self.cp * (phi * T[r["owner"]]).sum()
            elif a.get("frov") is not None:
                phi = flow_rate_outlet_flux(U[r["owner"]], r["normal"], r["area"], *a["frov"])
            elif a["pinned"]:
                phi = r["area"] * (a["Q"] / r["area"].sum())
            else:
                phi = (U[r["owner"]] * r["normal"]).sum(-1) * r["area"]
            sphi = phi.sum()
            out[f"return_T:{name}"] = (phi * Tf).sum() / sphi
            # Q_supply times the temperature rise, not sum(phi T) - Q T_sup: on the reference return sum(phi) is
            # not exactly Q (U_owner . S_f is not the discrete flux), and absolute kelvin times that imbalance is
            # tens of kW
            heat = self.rho * self.cp * a["Q"] * (out[f"return_T:{name}"] - a["T_sup"])
            out[f"heat_W:{name}"] = heat
            total = total + heat
        if self.servers:
            # one segment sum over all intake faces (a hall has hundreds of servers; a loop of small sums per call
            # cost the partitioned driver 28 ms per step)
            c = self._server_tables()
            w = c["area"] * (T[c["owner"]] + self.TRef)
            vals = torch.zeros(c["n"], dtype=w.dtype, device=w.device).index_add_(0, c["seg"], w) / c["asum"]
            for patch, v in zip(self.servers, vals.unbind(0)):
                out[f"inlet_T:{patch}"] = v
        out["heat_W:supply_flow"] = total
        if un is not None:
            total = h_ret - self.supply_enthalpy()
        elif return_enthalpy is not None:
            total = torch.as_tensor(float(return_enthalpy) - self.supply_enthalpy(), dtype=self.dtype, device=self.device)
        out["heat_W:total"] = total
        return out

    def vector(self, out: dict[str, torch.Tensor]) -> torch.Tensor:
        return torch.stack([out[k].reshape(()) for k in self.names])

    def compare(self, out: dict[str, torch.Tensor], reference_json: str) -> dict:
        """Differences against the HF reference (``reference/<FAMILY>.json``), in K and W."""
        ref = json.load(open(reference_json))
        rows = {}
        for name in self.acus:
            hf = ref["acus"].get(name)
            if hf is None:
                continue
            rows[f"return_T:{name}"] = float(out[f"return_T:{name}"]) - hf["returnFluxMeanK"]
            rows[f"heat_W:{name}"] = float(out[f"heat_W:{name}"]) - hf["heatRemovedW"]
        d = [float(out[f"inlet_T:{p}"]) - ref["servers"][p]["areaMeanK"] for p in self.servers if p in ref["servers"]]
        d = np.asarray(d)
        return dict(returns=rows,
                    inlet_rms_K=float(np.sqrt(np.mean(d ** 2))) if d.size else None,
                    inlet_max_K=float(np.max(np.abs(d))) if d.size else None,
                    heat_total_rel=float(out["heat_W:total"]) / ref["energyBalance"]["declaredItPowerW"] - 1.0)
