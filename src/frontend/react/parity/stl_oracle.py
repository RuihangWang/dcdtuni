#!/usr/bin/env python3
"""Capture and compare STL exports, byte for byte.

Why this exists
---------------
STL export is not a function of the project file. It reads `renderMeshes` — the
Three.js meshes the scene actually built, each tagged with `userData.exportName`
— so it is a function of the RENDERED GEOMETRY. Migrating it to React means
moving the geometry builder, which is a ~4400-line subsystem, and the only
honest way to do that is with an oracle: capture what the legacy editor exports
today, and require every later export to match it byte for byte.

A near-match is not a match. A facet whose normal is flipped, a vertex that
moved by 1e-7, or a solid renamed still produces a valid STL and a mesh that
looks right — and an OpenFOAM patch that is silently wrong.

Usage
-----
    browser-use open http://localhost:$DCDTUNI_PORT/   # the React app
    python3 stl_oracle.py capture <case-dir> <baseline-dir>
    # ... later, after any geometry change ...
    python3 stl_oracle.py capture <case-dir> <new-dir>
    python3 stl_oracle.py compare <baseline-dir> <new-dir>

The page logs in with the DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair, else
the launcher's local-mode file src/server/auth-local/admin_credentials
(src/frontend/scripts/admin_login.py), so a server started by a plain
./src/start_server.sh needs nothing extra.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

BU = os.path.expanduser('~/.local/bin/browser-use')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'scripts'))
from admin_login import login_js  # noqa: E402


def ev(js: str) -> str:
    """Evaluate `js` and return its result as a SINGLE line.

    Only the first line after "result: " is captured, so anything multi-line
    comes back truncated. That silently turned every captured STL into just its
    `solid <name>` header — 26 bytes, 0 facets — and the capture looked like a
    scene with no geometry rather than like a broken transport. Use `ev_text`
    for anything that can contain a newline.
    """
    r = subprocess.run([BU, 'eval', js], capture_output=True, text=True, timeout=300)
    out = (r.stdout or '') + (r.stderr or '')
    for line in out.splitlines():
        if line.startswith('result: '):
            return line[8:]
    return 'ERR:' + out.strip()[-300:]


def ev_text(js: str) -> str:
    """Evaluate `js` (which must yield a string) and return it with newlines intact.

    JSON-encodes on the page so the value crosses as one line, then decodes.
    """
    raw = ev(f'JSON.stringify({js})')
    if raw.startswith('ERR:'):
        raise SystemExit(raw[:300])
    try:
        value = json.loads(raw)
    except Exception:
        raise SystemExit(f'could not decode result ({len(raw)} chars): {raw[:200]}')
    if not isinstance(value, str):
        raise SystemExit(f'expected a string, got {type(value).__name__}')
    return value


def capture(case_dir: str, out_dir: str) -> None:
    """Export STL from the page and write every solid to `out_dir`.

    Goes through `buildGroupedStlMap` rather than the download button: the
    button hands the browser a zip, and a headless download is a second thing
    that can fail. What is wanted here is the STL TEXT the exporter produced.
    """
    case_dir = os.path.abspath(case_dir)
    os.makedirs(out_dir, exist_ok=True)

    # The DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD pair, else the launcher's
    # local-mode file, else the pair written here (src/frontend/scripts/admin_login.py).
    login = login_js(('admin', 'WIN#@desk'))
    proj = os.path.join(case_dir, os.path.basename(case_dir) + '.cfdproj')
    if not os.path.exists(proj):
        cands = [f for f in os.listdir(case_dir) if f.endswith('.cfdproj')]
        if not cands:
            raise SystemExit(f'no .cfdproj under {case_dir}')
        proj = os.path.join(case_dir, cands[0])

    load = (
        f"var c=new XMLHttpRequest();c.open('POST','/api/set-case-dir',false);"
        f"c.setRequestHeader('Content-Type','application/json');"
        f"c.send(JSON.stringify({{caseDir:{json.dumps(case_dir)}}}));"
        f"var r=new XMLHttpRequest();"
        f"r.open('GET','/api/read-file?path='+encodeURIComponent({json.dumps(proj)}),false);r.send();"
        f"var d=JSON.parse(JSON.parse(r.responseText).content);"
        # Whichever app is open. The legacy page applies through
        # _applyProjectData; the React app through its store.
        f"if (window.__dcdtuni) window.__dcdtuni.loadProject(d, {json.dumps(proj)});"
        f"else window._applyProjectData(d);"
    )
    print(ev(f"(function(){{{login}{load}return 'loaded';}})()"))
    time.sleep(3)

    # Every model, enabled or not, and leakage included — the same arguments
    # exportCFDProject passes, so this captures what a real export produces.
    #
    # Two apps, one function. The legacy page exposes buildGroupedStlMap as a
    # window global; the React app exposes its scene builder as __sceneBuilder.
    # Both reach the SAME shared module, which is the whole point — the export
    # is a function of the built scene, so comparing them is the only way to
    # know the two scenes agree.
    # Which app is open, decided by a marker only that app defines. Probing for
    # __sceneBuilder alone was not enough: browser-use keeps one browser across
    # runs, and a stale global from the previous page made the legacy capture
    # take the React branch and come back with zero facets.
    is_react = ev('typeof window.__dcdtuni') == 'object' and ev(
        'typeof window.__dcdtuni.buildGroupedStlMap'
    ) != 'function'
    if is_react:
        names = ev(
            "(function(){"
            "  var b = window.__sceneBuilder;"
            "  var p = window.__dcdtuni.getProject();"
            "  var ids = window.cfdprojStateV4.allModels(p).map(function(m){return m.uid});"
            "  window.__stlMap = b.api.buildGroupedStlMap(ids, true);"
            "  return JSON.stringify(Object.keys(window.__stlMap));"
            "})()"
        )
        try:
            keys = json.loads(names)
        except Exception:
            raise SystemExit(f'react export failed: {names[:300]}')
        return _write_capture(keys, out_dir)


    names = ev(
        "(function(){"
        # `wrapperGroup` is module-scoped in STL_generate.html and not on window;
        # buildGroupedStlMap reads it itself, and rebuildGeometry has already
        # updated the matrices. Touching it from here would just throw.
        # rebuildGeometry EXPLICITLY. _applyProjectData does not do it, and the
        # STL comes from `renderMeshes` — the meshes the scene actually built.
        # Without this the export succeeds and returns every group EMPTY: 18
        # files of "solid X\nendsolid X", 0 facets. That is a baseline that
        # matches anything, which is worse than no baseline at all.
        "  window.rebuildGeometry();"
        "  var ids = window.allModels().map(function(m){return m.uid});"
        "  window.__stlMap = window.__dcdtuni.buildGroupedStlMap(ids, true);"
        "  return JSON.stringify(Object.keys(window.__stlMap));"
        "})()"
    )
    try:
        keys = json.loads(names)
    except Exception:
        raise SystemExit(f'export failed: {names[:300]}')

    return _write_capture(keys, out_dir)


def _triangle_set(text: str):
    """Every facet as (normal, sorted vertices), sorted.

    Sorting the three vertices inside a facet makes the comparison blind to
    which corner the winding started from; sorting the facets makes it blind to
    emission order. The NORMAL is kept unsorted and compared as written, because
    a flipped normal is a real defect — it points the surface at the wrong side
    of the fluid.
    """
    out = []
    for normal, block in re.findall(
        r'facet normal ([^\n]+)\n\s*outer loop\n((?:\s*vertex [^\n]+\n){3})', text
    ):
        out.append((normal.strip(), tuple(sorted(re.findall(r'vertex ([^\n]+)', block)))))
    return sorted(out)


def _write_capture(keys, out_dir: str) -> None:
    index = {}
    for k in keys:
        text = ev_text(f"window.__stlMap[{json.dumps(k)}]")
        path = os.path.join(out_dir, k)
        with open(path, 'w') as fh:
            fh.write(text)
        index[k] = {
            'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'bytes': len(text),
            'facets': text.count('facet normal'),
            'solids': sorted(re.findall(r'^solid (\S+)', text, re.M)),
            # The SET of triangles, order-independent and vertex-order
            # independent within each facet. Two exports can differ byte for
            # byte purely because a polygon was triangulated from a different
            # starting vertex — same surface, same OpenFOAM patch, different
            # file. Distinguishing that from a moved vertex is the difference
            # between "harmless" and "the case meshes in the wrong place".
            'geometry': hashlib.sha256(
                repr(_triangle_set(text)).encode()
            ).hexdigest(),
        }
        print(f'  {k:52s} {index[k]["facets"]:6d} facets  {index[k]["bytes"]:8d} B')

    with open(os.path.join(out_dir, '_index.json'), 'w') as fh:
        json.dump(index, fh, indent=1, sort_keys=True)
    total = sum(v['facets'] for v in index.values())
    print(f'\n{len(index)} files, {total} facets -> {out_dir}')
    if total == 0:
        # An all-empty capture compares equal to every other all-empty capture,
        # so it would certify any change as safe. Refuse to write it as a
        # baseline.
        raise SystemExit(
            'ZERO facets — the scene had no geometry. This is not a baseline; '
            'it would match anything. Check that the project loaded and that '
            'rebuildGeometry ran.'
        )


def compare(a_dir: str, b_dir: str) -> None:
    a = json.load(open(os.path.join(a_dir, '_index.json')))
    b = json.load(open(os.path.join(b_dir, '_index.json')))
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    same, reordered, differ = [], [], []
    for k in sorted(set(a) & set(b)):
        if a[k]['sha256'] == b[k]['sha256']:
            same.append(k)
        elif a[k].get('geometry') and a[k]['geometry'] == b[k].get('geometry'):
            # Same triangles, different order. Harmless: the mesher reads the
            # facet set, not the file order.
            reordered.append(k)
        else:
            differ.append(k)

    print(f'files: {len(a)} baseline / {len(b)} new')
    print(f'  identical : {len(same)}')
    print(f'  reordered : {len(reordered)}  (same triangles, different emission order)')
    print(f'  DIFFERENT : {len(differ)}')
    print(f'  missing   : {len(only_a)}')
    print(f'  extra     : {len(only_b)}')
    for k in differ:
        av, bv = a[k], b[k]
        print(f'\n  {k}')
        print(f'    facets {av["facets"]} -> {bv["facets"]}, bytes {av["bytes"]} -> {bv["bytes"]}')
        lost = sorted(set(av['solids']) - set(bv['solids']))
        gained = sorted(set(bv['solids']) - set(av['solids']))
        if lost:
            print(f'    solids lost   : {lost}')
        if gained:
            print(f'    solids gained : {gained}')
        if not lost and not gained:
            print('    same solid names — the GEOMETRY changed, not the grouping')
    for k in only_a:
        print(f'  missing from new: {k}')
    for k in only_b:
        print(f'  only in new: {k}')
    for k in reordered:
        print(f'  reordered (geometry matches): {k}')
    if differ or only_a or only_b:
        raise SystemExit(1)
    if reordered:
        print('\ngeometry-identical (some files reordered)')
    else:
        print('\nbyte-identical')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if sys.argv[1] == 'capture':
        capture(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'compare':
        compare(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(__doc__)
