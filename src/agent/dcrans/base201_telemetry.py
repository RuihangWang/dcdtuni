#!/usr/bin/env python3
"""Base201 site telemetry -> the inputs of ``agent.dcrans`` (snapshots, observations, probes, sensor split).

The site export (``case/Base201/Ref_data/room201.npz``, 2,880 samples at 120 s, 2026-11-02 to 11-05, from
``ts_data1102-1105.csv``; ``b201_paths.json`` names the source objects) holds, per sample: the 48 in-row units'
fan speed, supply and return air temperatures (two sensors each, averaged), the 274 racks' measured power and the
148 room temperature sensors. Gaps are forward-filled per channel (a channel never measured is 0).

Names on the case side: a unit is ``_<last three characters of its name>`` (``列间空调P04`` -> ``_P04``, as the
boundary translation names it); a rack is its project name; a sensor is its project probe name, at the probe's
location in the project (``instrumentation.probes``).

    from base201_telemetry import Telemetry
    tm = Telemetry.load(repo / "case/Base201")
    snaps = tm.snapshots(range(k0, k0 + n))          # acu_speed, acu_T_sup_K, rack_heat_W, t_s
    obs = tm.observations(range(k0, k0 + n), sensors=train)   # {"probe name": C, "return_T:_P04": C} per step
    train, held = tm.split(0.7, seed=0)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

CADENCE_S = 120.0


def _ffill(a):
    a = np.array(a, dtype=float)
    for j in range(a.shape[1]):
        c = a[:, j]
        m = np.isnan(c)
        if not m.any():
            continue
        if m.all():
            c[:] = 0.0
            continue
        idx = np.where(~m, np.arange(len(c)), 0)
        np.maximum.accumulate(idx, out=idx)
        first = int(np.argmax(~m))
        c[:] = c[idx]
        c[:first] = c[first]
    return a


@dataclass
class Telemetry:
    time: np.ndarray          # (T,) epoch seconds (start to 1 ks, then the 120 s cadence)
    units: list               # case unit names (_P04, ...)
    speed: np.ndarray         # (T, U) %
    t_sup: np.ndarray         # (T, U) C
    t_ret: np.ndarray         # (T, U) C
    racks: list               # rack names
    rack_power_w: np.ndarray  # (T, R) W
    sensors: list             # sensor (probe) names
    sensor_t: np.ndarray      # (T, S) C
    sensor_xyz: np.ndarray    # (S, 3) project coordinates (y up)

    @classmethod
    def load(cls, case: Path, data: Path | None = None) -> "Telemetry":
        case = Path(case)
        data = Path(data) if data is not None else case / "Ref_data"
        d = np.load(data / "room201.npz")
        paths = json.loads((data / "b201_paths.json").read_text(encoding="utf-8"))
        units = ["_" + paths[n.rsplit(".", 1)[0]]["name"][-3:] for n in d["crah_FanSpeed_names"]]
        sensors = [n.rsplit(".", 2)[-2] for n in d["sensor_names"]]
        racks = [n.rsplit(".", 2)[-2] for n in d["rack_P_names"]]
        proj = json.loads(next(case.glob("*.cfdproj")).read_text(encoding="utf-8"))
        probes = {p["meta"]["name"]: p["geometry"]["location"] for p in proj["instrumentation"]["probes"].values()}
        missing = [s for s in sensors if s not in probes]
        if missing:
            raise ValueError(f"{len(missing)} sensors without a project probe, e.g. {missing[:3]}")
        xyz = np.array([[probes[s]["x"], probes[s]["y"], probes[s]["z"]] for s in sensors], dtype=float)
        # the export's time column is epoch kiloseconds truncated to integers (346 units over the 96 h of 2,880
        # samples); the samples are on the 120 s cadence, so the time is the sample index times the cadence
        tk = np.asarray(d["time"], dtype=np.int64)
        if len(tk) > 1 and not abs((tk[-1] - tk[0]) * 1e3 - (len(tk) - 1) * CADENCE_S) < 2e3:
            raise ValueError("the telemetry is not on a 120 s cadence")
        t = tk[0] * 1e3 + CADENCE_S * np.arange(len(tk), dtype=float)
        return cls(time=t, units=units, speed=_ffill(d["crah_FanSpeed"]),
                   t_sup=_ffill(0.5 * (d["crah_SupplyAirTemperature"] + d["crah_SupplyAirTemperature02"])),
                   t_ret=_ffill(0.5 * (d["crah_ReturnAirTemperature"] + d["crah_ReturnAirTemperature02"])),
                   racks=racks, rack_power_w=_ffill(d["rack_P"]) * 1e3, sensors=sensors,
                   sensor_t=_ffill(d["sensor"]), sensor_xyz=xyz)

    def split(self, frac: float = 0.7, seed: int = 0):
        """(train, held-out) sensor names: a seeded random spatial split, ``frac`` of the sensors for the fit."""
        rng = np.random.default_rng(int(seed))
        idx = set(rng.choice(len(self.sensors), int(round(frac * len(self.sensors))), replace=False).tolist())
        train = [s for i, s in enumerate(self.sensors) if i in idx]
        held = [s for i, s in enumerate(self.sensors) if i not in idx]
        return train, held

    def snapshots(self, ks: Iterable[int], *, supply_window: int = 1) -> list:
        """One dict per sample. supply_window (odd, samples): the supply temperatures as the centred moving mean over
        that many samples (clipped at the ends of the record). The units' supply sensors are sampled on their control
        cycle: on Base201 the O-row supplies alternate by 2-3 K from one 120 s sample to the next while the room
        sensors in front of them stay within 0.1 K, and a model driven by the raw values alternates with them."""
        ks = [int(k) for k in ks]
        t0 = self.time[ks[0]]
        h = max(int(supply_window), 1) // 2
        out = []
        for k in ks:
            lo, hi = max(k - h, 0), min(k + h + 1, len(self.time))
            ts = self.t_sup[lo:hi].mean(axis=0) if h else self.t_sup[k]
            out.append({"k": k, "t_s": float(self.time[k] - t0),
                        "acu_speed": {u: float(self.speed[k, j]) for j, u in enumerate(self.units)},
                        "acu_T_sup_K": {u: float(ts[j]) + 273.15 for j, u in enumerate(self.units)},
                        "rack_heat_W": {r: float(self.rack_power_w[k, j]) for j, r in enumerate(self.racks)}})
        return out

    def observations(self, ks: Iterable[int], *, sensors: Sequence[str] | None = None, returns: bool = True,
                     running_returns_only: bool = True) -> list:
        """One dict per step: the sensors' temperatures (C) by probe name and, with ``returns``, the units' return
        air temperature ``return_T:<unit>`` (only the running units' with ``running_returns_only``: a stopped unit's
        return sensor sees the aisle without the unit's flow)."""
        col = {s: i for i, s in enumerate(self.sensors)}
        names = list(self.sensors if sensors is None else sensors)
        out = []
        for k in (int(k) for k in ks):
            row = {s: float(self.sensor_t[k, col[s]]) for s in names}
            if returns:
                for j, u in enumerate(self.units):
                    if not running_returns_only or self.speed[k, j] > 0:
                        row[f"return_T:{u}"] = float(self.t_ret[k, j])
            out.append(row)
        return out
