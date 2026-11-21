import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInContext } from 'node:vm';
import { JSDOM } from 'jsdom';

const html = readFileSync(new URL('../web/STL_generate.html', import.meta.url), 'utf8');
const between = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const solveCode = between('    let _solveSubmitPending = false;', '    window.abortCurrentJob =');
const dialogCode = between('    function showSolvePartitionWarningDialog(', "    // mode: 'mesh' | 'solve' | 'full'");
const legacyCode = between('    window.runMeshGeneration =', '    const _freezeSkipIds =');
const transientCode = between('    window.runTransientSolve =', '    // ==================== End Transient');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness({ parallelSolve = true, solveProcess = 4, mesh, get } = {}) {
  const dom = new JSDOM('<button id="solve-run-btn">Solve</button><div id="solver-run-status"></div>', { runScripts: 'outside-only' });
  const { window } = dom;
  const config = { parallelSolve, solveProcess, meshProcess: 64 };
  const session = { validated: true, meshReady: true, activeJobId: null };
  const calls = [];
  let progressCount = 0;
  let streamCount = 0;
  Object.assign(window, {
    uiState: { session },
    state: {},
    t: (key, params) => key + (params ? ` ${JSON.stringify(params)}` : ''),
    simCfgMerged: () => config,
    alert: message => assert.fail(`Unexpected alert: ${message}`),
    wrapperGroup: { updateMatrixWorld() {} },
    buildCfdrunJsonV2: async () => ({ simulationConfig: { ...config } }),
    getCfdrunFilename: () => 'test.cfdrun',
    _updateSolveTabEnabled: () => { window.document.getElementById('solve-run-btn').disabled = !session.meshReady; },
    _createProgressOverlay: () => {
      progressCount++;
      return { setTitle() {}, setStep() {}, close() {} };
    },
    logOperation() {},
    smGetMonitoringConfig: () => ({ residuals: false, serverTemperature: [] }),
    _streamDctwinLog: () => { streamCount++; session._uiFrozen = true; },
    fetch: async (url, options = {}) => {
      calls.push({ url, ...options });
      if (url === '/api/check-mesh') {
        return get ? get(options) : { ok: true, json: async () => mesh || { meshReady: true, decomposed: true, nPartitions: 8 } };
      }
      assert.equal(url, '/api/run-mesh');
      return { ok: true, json: async () => ({ jobId: 'mock-job' }) };
    },
  });
  runInContext(dialogCode + solveCode + legacyCode + transientCode, dom.getInternalVMContext());
  return {
    window, config, session, calls,
    status: () => window.document.getElementById('solver-run-status').textContent,
    dialog: () => window.document.getElementById('solve-partition-warning-dialog'),
    click: id => window.document.getElementById(id).click(),
    progressCount: () => progressCount,
    streamCount: () => streamCount,
    posts: () => calls.filter(call => call.method === 'POST'),
    close: () => window.close(),
  };
}

test('HTML solve shows actual partition count and cancel preserves editable settings without POST', async t => {
  const h = harness(); t.after(h.close);
  h.window.document.getElementById('solve-run-btn').focus();
  const pending = h.window.runSolveOnly();
  await settle();
  assert.match(h.dialog().textContent, /"mesh":8,"solve":4/);
  assert.match(h.dialog().textContent, /dialog.solve_partition_parallel/);
  assert.equal(h.calls[0].cache, 'no-store');
  assert.equal(h.progressCount(), 0);
  assert.equal(h.session._uiFrozen, undefined);
  assert.equal(h.window.document.getElementById('solve-run-btn').disabled, false);
  assert.equal(h.posts().length, 0);
  h.click('solve-partition-cancel-btn');
  await pending;
  assert.equal(h.dialog(), null);
  assert.equal(h.posts().length, 0);
  assert.equal(h.config.solveProcess, 4);
  assert.equal(h.status(), 'status.solve_partition_cancelled');
  assert.equal(h.window.document.activeElement.id, 'solve-run-btn');
});

test('HTML solve continue submits once with current core count despite repeated clicks', async t => {
  let finishCheck;
  const h = harness({ get: () => new Promise(resolve => { finishCheck = resolve; }) }); t.after(h.close);
  const pending = h.window.runSolveOnly();
  await h.window.runSolveOnly();
  assert.equal(h.calls.length, 1);
  finishCheck({ ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 8 }) });
  await settle();
  await h.window.runSolveOnly();
  assert.equal(h.window.document.querySelectorAll('#solve-partition-warning-dialog').length, 1);
  h.click('solve-partition-continue-btn');
  await pending;
  await h.window.runSolveOnly();
  assert.equal(h.posts().length, 1);
  const payload = JSON.parse(h.posts()[0].body);
  assert.equal(JSON.parse(payload.cfdrunJson).simulationConfig.solveProcess, 4);
  assert.equal(payload.solveOnly, true);
  assert.deepEqual(payload.stlFiles, {});
  assert.equal(h.streamCount(), 1);
});

test('HTML solve matching partitions and undecomposed mesh submit without a warning', async t => {
  for (const mesh of [
    { meshReady: true, decomposed: true, nPartitions: 4 },
    { meshReady: true, decomposed: false, nPartitions: 0 },
  ]) {
    const h = harness({ mesh }); t.after(h.close);
    await h.window.runSolveOnly();
    assert.equal(h.dialog(), null);
    assert.equal(h.posts().length, 1);
  }
});

test('HTML serial solve warns about reconstruction and Escape cancels without altering config', async t => {
  for (const config of [
    { parallelSolve: false, solveProcess: 8 },
    { parallelSolve: true, solveProcess: 1 },
    { parallelSolve: false, solveProcess: 1, mesh: { meshReady: true, decomposed: true, nPartitions: 1 } },
  ]) {
    const h = harness(config); t.after(h.close);
    const pending = h.window.runSolveOnly();
    await settle();
    assert.match(h.dialog().textContent, new RegExp(`"mesh":${config.mesh?.nPartitions || 8},"solve":1`));
    assert.match(h.dialog().textContent, /dialog.solve_partition_serial/);
    assert.doesNotMatch(h.dialog().textContent, /dialog.solve_partition_parallel/);
    h.window.document.dispatchEvent(new h.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    await pending;
    assert.equal(h.posts().length, 0);
    assert.equal(h.dialog(), null);
    assert.equal(h.config.parallelSolve, config.parallelSolve);
    assert.equal(h.config.solveProcess, config.solveProcess);
  }
});

test('HTML failed mesh lookup blocks submission and releases the request guard for retry', async t => {
  let fails = true;
  const h = harness({ get: async () => {
    if (fails) throw new Error('offline');
    return { ok: true, json: async () => ({ meshReady: true, decomposed: false, nPartitions: 0 }) };
  } }); t.after(h.close);
  await h.window.runSolveOnly();
  assert.equal(h.posts().length, 0);
  assert.equal(h.progressCount(), 0);
  assert.equal(h.status(), 'error.solve_partition_check_failed');
  fails = false;
  await h.window.runSolveOnly();
  assert.equal(h.posts().length, 1);
});

test('HTML uses settings edited while the fresh mesh lookup is pending', async t => {
  let finishCheck;
  const h = harness({ get: () => new Promise(resolve => { finishCheck = resolve; }) }); t.after(h.close);
  const pending = h.window.runSolveOnly();
  h.config.solveProcess = 8;
  finishCheck({ ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 8 }) });
  await pending;
  assert.equal(h.dialog(), null);
  assert.equal(h.posts().length, 1);
  assert.equal(JSON.parse(JSON.parse(h.posts()[0].body).cfdrunJson).simulationConfig.solveProcess, 8);
});

test('HTML aborts a mesh lookup after ten seconds without submitting', async t => {
  let fireTimeout;
  let cleared = false;
  const h = harness({ get: options => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('timeout')));
  }) }); t.after(h.close);
  h.window.setTimeout = (callback, delay) => {
    assert.equal(delay, 10000);
    fireTimeout = callback;
    return 123;
  };
  h.window.clearTimeout = timer => { assert.equal(timer, 123); cleared = true; };
  const pending = h.window.runSolveOnly();
  fireTimeout();
  await pending;
  assert.equal(cleared, true);
  assert.equal(h.posts().length, 0);
  assert.equal(h.progressCount(), 0);
  assert.equal(h.status(), 'error.solve_partition_check_failed');
});

test('HTML HTTP errors and malformed partition counts cannot silently submit', async t => {
  for (const response of [
    { ok: false, json: async () => ({}) },
    { ok: true, json: async () => ({ meshReady: true, decomposed: true, nPartitions: 0 }) },
    { ok: true, json: async () => ({}) },
  ]) {
    const h = harness({ get: async () => response }); t.after(h.close);
    await h.window.runSolveOnly();
    assert.equal(h.posts().length, 0);
    assert.equal(h.status(), 'error.solve_partition_check_failed');
  }
});

test('HTML fresh lookup detects removed mesh and clears stale readiness', async t => {
  const h = harness({ mesh: { meshReady: false, reason: 'No case directory set' } }); t.after(h.close);
  await h.window.runSolveOnly();
  assert.equal(h.posts().length, 0);
  assert.equal(h.session.meshReady, false);
  assert.equal(h.status(), 'error.mesh_first');
});

test('HTML transient and legacy solve entry points both use the partition warning', async t => {
  for (const start of [h => h.window.runTransientSolve(), h => h.window.runMeshGeneration('solve')]) {
    const h = harness(); t.after(h.close);
    const pending = start(h);
    await settle();
    assert.ok(h.dialog());
    assert.equal(h.posts().length, 0);
    h.click('solve-partition-cancel-btn');
    await pending;
    await settle();
    assert.equal(h.posts().length, 0);
  }
});
