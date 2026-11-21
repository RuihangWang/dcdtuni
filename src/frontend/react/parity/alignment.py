#!/usr/bin/env python3
"""The React -> HTML control map, and what "aligned" means for each pair.

Alignment is not "both exist". For each pair it says what has to MATCH:

  value    the two read the same value off the same loaded project
  effect   acting on either produces the same change in the project data
  present  the control exists on both sides with the same enabled/disabled state
  na       no HTML counterpart (React-only), or vice versa — must be justified
"""

# React path (or label) -> (HTML selector, what must match, note)
MAP = {
    # ── file bar ────────────────────────────────────────────────────────────
    '📂 加载工程':        ('.file-bar button:nth-of-type(1)', 'effect',  'both load a .cfdproj into the editor'),
    '💾 保存工程':        ('.file-bar button:nth-of-type(2)', 'effect',  'both write the project back'),
    '🏠 房间 2D 平面设计': ('.file-bar button:nth-of-type(3)', 'present', 'React: disabled, 2D editor not migrated'),
    '🎮 进入第一人称':     ('#fps-toggle-btn',                 'present', 'React: disabled, FPS not migrated'),
    '💧 液冷模拟':        ('#btn-mode-liquid',                'present', 'React switches the creation toolbar only'),
    '🌬️ 风冷模拟':       ('#btn-mode-air',                   'present', ''),

    # ── left tabs ───────────────────────────────────────────────────────────
    '🗂️ 几何与属性': ('#tab-btn-geometry',    'present', ''),
    '⚙️ 网格':      ('#tab-btn-mesh',        'present', 'gated on server-info allOk in both'),
    '🔬 求解':      ('#tab-btn-solver',      'present', 'gated on a mesh existing in both'),
    '⏱ 瞬态':      ('#tab-btn-transient',   'na',      'React: retired 2026-09-30 — the timeline in the top bar replaces it (cfdproj v5 tracks)'),
    '📊 后处理':    ('#tab-btn-postprocess', 'present', 'gated on solve results in both'),
    '⚙ System':    ('#tab-btn-system',      'present', ''),

    # ── tree header ─────────────────────────────────────────────────────────
    '#tree-sort-badge': ('#tree-sort-badge', 'effect',  'cycles A→Z / Z→A / manual, same localStorage key'),
    '#mode-badge':      ('#mode-badge',      'value',   'both render tree.mode_multi with the model count'),

    # ── export ──────────────────────────────────────────────────────────────
    '📥 导出 CFD 流形拓扑 STL':     ('#left-geo-export button:nth-of-type(1)', 'present', 'React: disabled, STL export not migrated'),
    '🔬 导出 CFD 工程包 (JSON+STL)': ('#left-geo-export button:nth-of-type(2)', 'present', 'React: disabled, cfdrun export not migrated'),

    # ── toolbar ─────────────────────────────────────────────────────────────
    '#toolbar-collapse-btn': ('#toolbar-collapse-btn', 'effect',  'collapses the toolbar'),
    '#rot-snap-toggle':      ('#rot-snap-toggle',      'present', 'React: view flag only, no gizmo yet'),
    '#snap-face-btn':        ('#snap-face-btn',        'present', 'React: view flag only'),
    '#snap-edge-btn':        ('#snap-edge-btn',        'present', 'React: view flag only'),
    '#snap-grid-btn':        ('#snap-grid-btn',        'effect',  'mutually exclusive with face/edge in both'),
    '#grid-toggle-btn':      ('#grid-toggle-btn',      'effect',  'toggles the XOZ background grid in the viewport'),
    '#light-toggle-btn':     ('#light-toggle-btn',     'effect',  'toggles scene lights'),
    '#grid-density-slider':  ('#grid-density-slider',  'effect',  'drives the grid spacing badge'),
    '#lang-selector':        ('#lang-selector',        'effect',  'same dictionaries, same storage key'),
    '#theme-selector':       ('#theme-selector',       'present', 'React: single theme so far'),
    '#view-cube':            ('#view-cube',            'effect',  'click a face to snap the camera'),

    # ── mesh tab ────────────────────────────────────────────────────────────
    '#mesh-run-btn': ('#mesh-run-btn', 'effect', 'React posts run-mesh without cfdrunJson/stlFiles: re-meshes the last export'),

    # ── solver tab ──────────────────────────────────────────────────────────
    '#solve-run-btn': ('#solve-run-btn', 'effect', 'same endpoint, solveOnly'),

    # ── log panel ───────────────────────────────────────────────────────────
    '↶ 撤销': (None, 'na', 'React-only: the HTML editor has no undo button, only Ctrl+Z'),
    '↷ 重做': (None, 'na', 'React-only: same'),
}

# Mesh/solver fields align by the state key they write, not by element id.
# React id -> (HTML id, runDefaults path)
FIELD_MAP = {
    'meshBaseSize':            ('ms-base-size',   'meshConfig.meshBaseSize'),
    'maxGlobalCells':          ('ms-max-cells',   'meshConfig.maxGlobalCells'),
    'parallelMesh':            ('ms-parallel',    'meshConfig.parallelMesh'),
    'meshProcess':             ('ms-procs',       'meshConfig.meshProcess'),
    'meshMode':                ('ms-mesh-mode',   'meshConfig.meshMode'),
    'firstCellSize':           ('ms-first-cell',  'meshConfig.firstCellSize'),
    'refinementLevel':         ('ms-rl-0/1',      'meshConfig.refinementLevel'),
    'defaultFeatureLevel':     ('ms-feat-default','meshConfig.defaultFeatureLevel'),
    'rackRegionLevel':         ('ms-rack-region', 'meshConfig.rackRegionLevel'),
    'acuRefinementLevel':      ('ms-acu-0/1',     'meshConfig.acuRefinementLevel'),
    'acuFeatureLevel':         ('ms-feat-acu',    'meshConfig.acuFeatureLevel'),
    'boxRefinementLevel':      ('ms-box-0/1',     'meshConfig.boxRefinementLevel'),
    'boxFeatureLevel':         ('ms-feat-box',    'meshConfig.boxFeatureLevel'),
    'partitionWallRefinementLevel': ('ms-pw-0/1', 'simulationConfig.partitionWallRefinementLevel'),
    'partitionWallFeatureLevel':    ('ms-feat-pw','simulationConfig.partitionWallFeatureLevel'),
    'wallBafflesRefinementLevel':   ('ms-bfl-0/1','meshConfig.wallBafflesRefinementLevel'),
    'baffleFeatureLevel':      ('ms-feat-baffle', 'meshConfig.baffleFeatureLevel'),
    'floorRefinementLevel':    (None,             'meshConfig.floorRefinementLevel'),
    'floorFeatureLevel':       (None,             'meshConfig.floorFeatureLevel'),
    'meshDetectBaseSize':      ('ms-detect-base', 'meshConfig.meshDetectBaseSize'),
    'locationInMesh':          ('slv-loc-x/y/z',  'meshConfig.locationInMesh'),
    'locInMeshVisible':        ('loc-visible-cb', 'meshConfig.locInMeshVisible'),
    'solverType':              ('transient-mode-selector', 'simulationConfig.solverType'),  # top bar, its one writer (T2c)
    'endTime':                 ('slv-endtime',    'simulationConfig.endTime'),
    'writeInterval':           ('slv-writeinterval', 'simulationConfig.writeInterval'),
    'writePrecision':          ('slv-write-precision', 'simulationConfig.writePrecision'),
    'endTimeSeconds':          ('timeline-duration', 'simulationConfig.endTimeSeconds'),  # mirrors transient.durationS (T2c)
    'startFrom':               ('slv-start-from', 'simulationConfig.startFrom'),
    'adjustTimeStep':          ('slv-dt-*',       'simulationConfig.adjustTimeStep'),
    'deltaT':                  ('slv-delta-t',    'simulationConfig.deltaT'),
    'maxDeltaT':               ('slv-max-delta-t','simulationConfig.maxDeltaT'),
    'maxCo':                   ('slv-max-co',     'simulationConfig.maxCo'),
    'writeIntervalSeconds':    ('slv-write-interval-s', 'simulationConfig.writeIntervalSeconds'),
    'ddtScheme':               ('slv-ddt-scheme', 'simulationConfig.ddtScheme'),
    'crankNicolsonCoeff':      ('slv-cn-coeff',   'simulationConfig.crankNicolsonCoeff'),
    'nOuterCorrectors':        ('slv-n-outer',    'simulationConfig.nOuterCorrectors'),
    'nCorrectors':             ('slv-n-correctors','simulationConfig.nCorrectors'),
    'parallelSolve':           ('slv-parallel',   'simulationConfig.parallelSolve'),
    'solveProcess':            ('slv-procs',      'simulationConfig.solveProcess'),
    'gravityX':                ('slv-gx',         'simulationConfig.gravityX'),
    'gravityY':                ('slv-gy',         'simulationConfig.gravityY'),
    'gravityZ':                ('slv-gz',         'simulationConfig.gravityZ'),
    'solverPreset':            ('preset-*',       'simulationConfig.solverPreset'),
    'nonOrthoCorrection':      ('slv-nonortho',   'simulationConfig.nonOrthoCorrection'),
    'nNonOrthogonalCorrectors':('slv-nnonortho',  'simulationConfig.nNonOrthogonalCorrectors'),
    'schemes.divSchemeU':      ('slv-div-U',      'simulationConfig.schemes.divSchemeU'),
    'schemes.divSchemeT':      ('slv-div-T',      'simulationConfig.schemes.divSchemeT'),
    'schemes.divSchemeK':      ('slv-div-ke',     'simulationConfig.schemes.divSchemeK'),
    'schemes.laplacianScheme': ('slv-laplacian',  'simulationConfig.schemes.laplacianScheme'),
    'schemes.snGradScheme':    ('slv-sngrad',     'simulationConfig.schemes.snGradScheme'),
    'relaxationFactors.p_rgh': ('slv-relax-p',    'simulationConfig.relaxationFactors.p_rgh'),
    'relaxationFactors.U':     ('slv-relax-u',    'simulationConfig.relaxationFactors.U'),
    'relaxationFactors.T':     ('slv-relax-t',    'simulationConfig.relaxationFactors.T'),
    'relaxationFactors.k_epsilon': ('slv-relax-ke','simulationConfig.relaxationFactors.k_epsilon'),
    'residualControl.p_rgh':   ('slv-res-prgh',   'simulationConfig.residualControl.p_rgh'),
    'residualControl.U':       ('slv-res-U',      'simulationConfig.residualControl.U'),
    'residualControl.T':       ('slv-res-T',      'simulationConfig.residualControl.T'),
    'residualControl.k_epsilon':('slv-res-kepsilon','simulationConfig.residualControl.k_epsilon'),
}

if __name__ == '__main__':
    print(f'{len(MAP)} named control pairs, {len(FIELD_MAP)} config fields')
    print('React-only:', [k for k, v in MAP.items() if v[0] is None])
    print('no HTML control:', [k for k, v in FIELD_MAP.items() if v[0] is None])
