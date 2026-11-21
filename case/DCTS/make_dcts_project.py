"""A DCTS room as a project of the current editor, with the recipe the dataset was built with.

    python case/DCTS/make_dcts_project.py <dataset cfdproj> <out case dir> [--name NAME]

The input is a DCTS room's cfdproj as the dataset generated it (the regeneration kit's
1_cfdproj/cfdproj_final_500.tar.gz, NOT case/GTC_dataset/case_*/, whose run defaults a later
migration rewrote). The output keeps the room and applies what the 2026-10-03 JUPITER regeneration ran
(dcdtuni 7b53f528; SCANO docs/dcts_regen_jupiter.md section 7):
  mesh      snappyHexMesh (meshEngine "snappy"), the dataset's meshConfig as stored (base cell 0.34 m, the
            refinement levels), 24 processes;
  solve     5000 SIMPLE iterations, relaxation p_rgh 0.3, U 0.5, T 0.5, k-epsilon 0.3, 24 processes;
  baffles   fully open: the dataset's runner treated every cyclic baffle as plain cyclic whatever its
            porosity, so the false ceiling's perforation goes to 100 % open area and every rack's blanking
            panels to "fullyOpen" (sigma 1, no porousBafflePressure).
Two boundary conditions of the current runner still differ from the dataset (the damped server-outlet
temperature feedback and the ACU returns pinned to their supply flow); the AI solve rewrites their first-
iteration values into the dataset's form (backend.aircooling.ai.dcts_cache.to_dataset_form).
"""
import argparse
import json
from pathlib import Path

RELAXATION = {'p_rgh': 0.3, 'U': 0.5, 'T': 0.5, 'k_epsilon': 0.3}


def convert(project: dict, case_rel: str, name: str) -> dict:
    project['name'] = name
    project['meta'].update(projectName=name, exportFileName=name, casePath=case_rel)
    run = project['runDefaults']
    run['meshConfig'].update(meshEngine='snappy', meshProcess=24)
    run['simulationConfig'].update(endTime=5000, writeInterval=5000, solveProcess=24, parallelSolve=True,
                                   relaxationFactors=dict(RELAXATION))

    def open_perforations(o):
        if isinstance(o, dict):
            if isinstance(o.get('openAreaPct'), (int, float)):
                o['openAreaPct'] = 100
            for v in o.values():
                open_perforations(v)
        elif isinstance(o, list):
            for v in o:
                open_perforations(v)
    open_perforations(project['constructions'].get('floors', {}))
    for rack in project['constructions'].get('racks', {}).values():
        rack.setdefault('cooling', {})['blankingPanelType'] = 'fullyOpen'
    return project


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cfdproj')
    ap.add_argument('out')
    ap.add_argument('--name')
    a = ap.parse_args()
    repo = Path(__file__).resolve().parents[2]
    out = Path(a.out).resolve()
    name = a.name or out.name
    project = convert(json.loads(Path(a.cfdproj).read_text()), str(out.relative_to(repo)), name)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{name}.cfdproj').write_text(json.dumps(project, indent=2, ensure_ascii=False))
    print(out / f'{name}.cfdproj')


if __name__ == '__main__':
    main()
