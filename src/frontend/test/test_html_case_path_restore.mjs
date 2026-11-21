import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInContext } from 'node:vm';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../web/STL_generate.html', import.meta.url), 'utf8');
const between = (start, end) => {
  const from = html.indexOf(start);
  const to = html.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `Missing HTML code boundaries: ${start}`);
  return html.slice(from, to);
};
const loadCode = between('    window._applyProjectData =', '    window.loadProject =');
const integrationCode = between('    let _caseDirRevision =', '    // ---- Server-side folder browser dialog ----');
const scanCode = between('    window.scanCaseStatus =', '    // Refresh post-processing results');
const readinessCode = between('    function _updateMeshTabEnabled()', '    // ═══════════════════════════════════════════════════════════════════');
const displayCode = between('    function renderDctwinConfigSection()', '    function _renderRoomTempUI()');
const checkCode = between('    window.checkMeshReady =', '    function _syncMeshPanelInputs()');
const settle = () => new Promise(resolve => setImmediate(resolve));
const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300, status, statusText: `HTTP ${status}`,
  json: async () => data, text: async () => JSON.stringify(data),
});
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

function harness({ intercept, scan } = {}) {
  const dom = new JSDOM(`<input id="dctwin-case-input" value="/previous-hidden-path">
    <button id="dctwin-setdir-btn"></button><button id="tab-btn-mesh"></button>
    <button id="tab-btn-solver"></button><button id="solve-run-btn"></button>
    <button id="tab-btn-postprocess"></button><button id="btn-init-postprocess"></button>
    <button id="check-mesh-btn"></button><div id="mesh-check-status"></div>`,
  { runScripts: 'outside-only' });
  const { window } = dom;
  const calls = [];
  const checkpoints = [];
  const logs = [];
  let serverCase = '/server-startup';
  let dropdownRuns = [];
  const ui = () => ({ selection: { modelId: null }, session: {
    validated: false, meshReady: false, solveComplete: false, _serverInfo: null,
    logRuns: [], liquidRuns: [], activeTab: 'geometry',
  } });
  Object.assign(window, {
    state: { uid: 'initial', meta: { casePath: '' } }, uiState: ui(),
    migrateCfdprojToV4: data => ({ ...data, schemaVersion: 4 }),
    cfdprojStateV4: { normalizeStateV4: data => structuredClone(data) },
    normalizeUiState: ui, loadUiPrefs: () => ({}),
    pushUndoCheckpoint: name => checkpoints.push(name),
    logOperation: (...args) => logs.push(args),
    t: (key, params) => key + (params ? ` ${JSON.stringify(params)}` : ''),
    alert: message => assert.fail(`Unexpected alert: ${message}`),
    isLiquidMode: () => false, globalGroup: {}, redoStack: [],
    _populateLogRunDropdown: runs => { dropdownRuns = Array.from(runs); },
    fetch: async (url, options = {}) => {
      const call = { url, ...options, body: options.body ? JSON.parse(options.body) : undefined };
      calls.push(call);
      const defaultResponse = () => {
        if (url === '/api/set-case-dir') {
          serverCase = call.body.caseDir;
          return response({ ok: true, caseDir: serverCase });
        }
        if (url === '/api/server-info') return response({
          allOk: true, dctwinAvailable: true, caseDirOk: true, caseDir: serverCase,
        });
        if (url === '/api/scan-case-status') return response(scan || {
          meshReady: true, solveReady: true, postprocessReady: true,
          logRuns: [{ dir: `${serverCase}/Run-1`, timesteps: [500] }], liquidRuns: [],
        });
        assert.fail(`Unexpected request: ${url}`);
      };
      return intercept ? intercept(call, defaultResponse) : defaultResponse();
    },
  });
  for (const name of [
    '_disposeResizeHandles', 'saveUiPrefs', '_rebuildModelMap', '_rebuildModelGroupMap',
    'syncCounters', 'restoreGeometryCache', 'resetWrapperTransform', 'rebuildBackgroundGrid',
    'destroyMoveGizmo', 'rebuildGeometry', '_rebuildAllExtraLocSpheres', 'fitCameraToScene',
    '_applyLeftTabUI', 'rememberRemoteProjectPath', 'clearCurrentRemoteProjectFile', 'updateUI',
  ]) window[name] = () => {};
  runInContext(integrationCode + scanCode + readinessCode + loadCode + displayCode + checkCode, dom.getInternalVMContext());
  return {
    window, calls, checkpoints, logs,
    state: () => window.state,
    session: () => window.uiState.session,
    serverCase: () => serverCase,
    dropdownRuns: () => dropdownRuns,
    posts: () => calls.filter(call => call.method === 'POST'),
    load: (path, uid = 'project') => {
      window._applyProjectData({ uid, meta: { casePath: path } }, `${uid}.cfdproj`);
      return window._casePathRestorePromise;
    },
    disabled: id => window.document.getElementById(id).disabled,
    inspector: () => runInContext('renderDctwinConfigSection()', dom.getInternalVMContext()),
    close: () => window.close(),
  };
}

test('HTML project load applies the saved directory and discovers existing results automatically', async t => {
  const h = harness(); t.after(h.close);
  await h.load('/existing-case');
  assert.equal(h.state().meta.casePath, '/existing-case');
  assert.equal(h.serverCase(), '/existing-case');
  assert.deepEqual(h.calls.map(call => call.url), ['/api/set-case-dir', '/api/server-info', '/api/scan-case-status']);
  assert.deepEqual(h.posts()[0].body, { caseDir: '/existing-case', createIfMissing: false });
  assert.equal(h.session().validated, true);
  assert.equal(h.session().meshReady, true);
  assert.equal(h.session().solveComplete, true);
  for (const id of ['tab-btn-mesh', 'tab-btn-solver', 'solve-run-btn', 'tab-btn-postprocess', 'btn-init-postprocess']) {
    assert.equal(h.disabled(id), false, id);
  }
  assert.equal(h.dropdownRuns()[0].dir, '/existing-case/Run-1');
  assert.deepEqual(h.checkpoints, ['loadProject']);
  assert.ok(h.logs.every(log => log[3] === 'file'));
});

test('HTML only clears a saved path when the backend confirms missing or non-directory', async t => {
  for (const [status, code] of [[404, 'case_dir_missing'], [400, 'case_dir_not_directory']]) {
    const h = harness({ intercept: () => response({ ok: false, code, error: 'invalid path' }, status) });
    t.after(h.close);
    await h.load('/invalid-case');
    assert.equal(h.state().meta.casePath, '');
    assert.equal(h.session().validated, false);
    assert.equal(h.session().meshReady, false);
    assert.equal(h.session().solveComplete, false);
    assert.equal(h.calls.length, 1);
    assert.ok(h.logs.some(log => log[1].includes('log.case_path_invalid')));
  }
});

test('HTML retains saved paths on offline, auth, permission, server and malformed-response failures', async t => {
  for (const failure of [
    () => { throw new Error('offline'); },
    () => response({ error: 'authentication required' }, 401),
    () => response({ error: 'permission denied' }, 403),
    () => response({ error: 'server error' }, 500),
    () => response({ error: 'route missing' }, 404),
    () => ({ ok: true, json: async () => { throw new Error('Unexpected HTML'); } }),
  ]) {
    const h = harness({ intercept: failure }); t.after(h.close);
    await h.load('/saved-case');
    assert.equal(h.state().meta.casePath, '/saved-case');
    assert.equal(h.session().validated, false);
    assert.match(h.inspector(), /value="\/saved-case"/);
    assert.ok(h.logs.some(log => log[1].includes('log.case_path_restore_failed')));
    assert.ok(h.logs.every(log => !log[1].includes('log.case_path_invalid')));
  }
});

test('HTML server-info failure after directory restoration preserves the saved path', async t => {
  const h = harness({ intercept: (call, fallback) => {
    if (call.url === '/api/server-info') throw new Error('disconnected');
    return fallback();
  } }); t.after(h.close);
  await h.load('/saved-case');
  assert.equal(h.state().meta.casePath, '/saved-case');
  assert.equal(h.session().validated, false);
  assert.equal(h.calls.length, 2);
  assert.ok(h.logs.some(log => log[1].includes('log.case_path_restore_failed')));
});

test('HTML reconnects using the retained directory after a temporary connection failure', async t => {
  let offline = true;
  const h = harness({ intercept: (call, fallback) => {
    if (offline) throw new Error('offline');
    return fallback();
  } }); t.after(h.close);
  await h.load('/saved-case');
  assert.equal(h.state().meta.casePath, '/saved-case');
  assert.equal(h.session().validated, false);
  offline = false;
  const info = await h.window.refreshServerInfo();
  assert.equal(info.caseDir, '/saved-case');
  assert.equal(h.serverCase(), '/saved-case');
  assert.equal(h.session().validated, true);
  assert.equal(h.session().meshReady, true);
  assert.equal(h.posts().at(-1).body.createIfMissing, false);
  assert.deepEqual(h.checkpoints, ['loadProject']);
});

test('HTML serializes in-flight project changes and leaves both server and UI on the latest file', async t => {
  const first = deferred();
  const h = harness({ intercept: async (call, fallback) => {
    if (call.url === '/api/set-case-dir' && call.body.caseDir === '/case-a') await first.promise;
    return fallback();
  } }); t.after(h.close);
  const a = h.load('/case-a', 'same-uid');
  await settle();
  const b = h.load('/case-b', 'same-uid');
  await settle();
  assert.equal(h.posts().length, 1);
  assert.equal(h.session().validated, false);
  first.resolve();
  await Promise.all([a, b]);
  assert.deepEqual(h.posts().map(call => call.body.caseDir), ['/case-a', '/case-b']);
  assert.equal(h.serverCase(), '/case-b');
  assert.equal(h.state().meta.casePath, '/case-b');
  assert.equal(h.session()._serverInfo.caseDir, '/case-b');
  assert.equal(h.dropdownRuns()[0].dir, '/case-b/Run-1');
});

test('HTML ignores checkMesh completion from the previous project', async t => {
  const h = harness({ intercept: (call, fallback) => {
    if (call.url === '/api/check-mesh') return response({ meshReady: true });
    if (call.url === '/api/run-check-mesh') return response({ jobId: 'old-check' });
    return fallback();
  } }); t.after(h.close);
  let stream;
  h.window.EventSource = class {
    constructor() { stream = this; }
    close() { this.closed = true; }
  };
  h.window._appendDctwinLogLine = () => {};
  await h.load('/case-a');
  await h.window.checkMeshReady();
  assert.equal(h.disabled('check-mesh-btn'), true);
  await h.load('', 'case-b');
  stream.onmessage({ data: JSON.stringify({ done: true, returncode: 0, checkMeshOk: true }) });
  assert.equal(stream.closed, true);
  assert.equal(h.session().meshReady, false);
  assert.equal(h.disabled('tab-btn-solver'), true);
  assert.equal(h.disabled('check-mesh-btn'), false);
});

test('HTML discards an earlier server-info response after a project load', async t => {
  const oldInfo = deferred();
  let firstInfo = true;
  const h = harness({ intercept: (call, fallback) => {
    if (call.url === '/api/server-info' && firstInfo) {
      firstInfo = false;
      return oldInfo.promise;
    }
    return fallback();
  } }); t.after(h.close);
  const initialRefresh = h.window.refreshServerInfo();
  await settle();
  await h.load('/new-case');
  oldInfo.resolve(response({ allOk: true, caseDirOk: true, caseDir: '/old-server-case' }));
  await initialRefresh;
  assert.equal(h.state().meta.casePath, '/new-case');
  assert.equal(h.session()._serverInfo.caseDir, '/new-case');
  assert.equal(h.session().validated, true);
});

test('HTML empty-path file does not inherit the previous case or server startup path', async t => {
  const h = harness(); t.after(h.close);
  await h.load('/case-a');
  await h.load('', 'empty-path');
  assert.equal(h.state().meta.casePath, '');
  assert.equal(h.session().validated, false);
  assert.equal(h.session().meshReady, false);
  assert.equal(h.session().solveComplete, false);
  assert.equal(h.dropdownRuns().length, 0);
  await h.window.refreshServerInfo();
  await h.window.scanCaseStatus();
  assert.equal(h.state().meta.casePath, '');
  assert.equal(h.session()._serverInfo.caseDir, '');
  assert.equal(h.session().validated, false);
  assert.equal(h.posts().length, 1);
  assert.equal(h.calls.filter(call => call.url === '/api/scan-case-status').length, 1);
});

test('HTML discards an older status scan after loading a file with no working directory', async t => {
  const oldScan = deferred();
  let delayScan = false;
  const h = harness({ intercept: (call, fallback) => {
    if (call.url === '/api/scan-case-status' && delayScan) return oldScan.promise;
    return fallback();
  } }); t.after(h.close);
  await h.load('/case-a');
  delayScan = true;
  const scan = h.window.scanCaseStatus();
  await settle();
  await h.load('', 'no-path');
  oldScan.resolve(response({
    meshReady: true, postprocessReady: true, logRuns: [{ dir: '/case-a/old-run' }],
  }));
  await scan;
  assert.equal(h.state().meta.casePath, '');
  assert.equal(h.session().validated, false);
  assert.equal(h.session().meshReady, false);
  assert.equal(h.session().solveComplete, false);
  assert.equal(h.dropdownRuns().length, 0);
});

test('HTML an in-flight restore cannot repopulate an empty-path project', async t => {
  const post = deferred();
  const h = harness({ intercept: async (call, fallback) => {
    if (call.url === '/api/set-case-dir') await post.promise;
    return fallback();
  } }); t.after(h.close);
  const a = h.load('/case-a');
  await settle();
  await h.load('', 'no-path');
  post.resolve();
  await a;
  await h.window.refreshServerInfo();
  assert.equal(h.state().meta.casePath, '');
  assert.equal(h.session().validated, false);
  assert.equal(h.session()._serverInfo.caseDir, '');
  assert.equal(h.calls.filter(call => call.url === '/api/scan-case-status').length, 0);
});

test('HTML blank initial project can still use the server startup directory', async t => {
  const h = harness(); t.after(h.close);
  await h.window.refreshServerInfo();
  assert.equal(h.state().meta.casePath, '/server-startup');
  assert.equal(h.session().validated, true);
  assert.equal(h.posts().length, 0);
});

test('HTML manual directory selection retains directory creation and one undo checkpoint', async t => {
  const h = harness(); t.after(h.close);
  await h.window.applyDctwinCaseDir('/manually-selected');
  assert.deepEqual(h.posts()[0].body, { caseDir: '/manually-selected', createIfMissing: true });
  assert.equal(h.state().meta.casePath, '/manually-selected');
  assert.deepEqual(h.checkpoints, ['applyDctwinCaseDir']);
});

test('HTML restoring a case without results clears stale mesh and postprocess readiness', async t => {
  const h = harness({ scan: { meshReady: false, postprocessReady: false, logRuns: [], liquidRuns: [] } });
  t.after(h.close);
  Object.assign(h.session(), { meshReady: true, solveComplete: true, logRuns: [{ dir: '/old-run' }] });
  await h.load('/empty-case');
  assert.equal(h.session().validated, true);
  assert.equal(h.session().meshReady, false);
  assert.equal(h.session().solveComplete, false);
  assert.equal(h.disabled('solve-run-btn'), true);
  assert.equal(h.disabled('tab-btn-postprocess'), true);
  assert.equal(h.dropdownRuns().length, 0);
});
