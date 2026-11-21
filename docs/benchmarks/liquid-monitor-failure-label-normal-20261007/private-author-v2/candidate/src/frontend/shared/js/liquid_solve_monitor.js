/**
 * liquid_solve_monitor.js
 * Floating convergence monitor window for ODIL liquid cooling solver.
 * Mirrors the air-mode _sm* monitoring architecture with _lsm* prefix.
 */

(function() {
    'use strict';

    function t(k, v) { return (window.t || function(x) { return x; })(k, v); }

    window._liquidSolverMonitor = null;
    window._lsmActiveTab = 'loss';
    window._lsmHiddenSeries = {};
    window._lsmFontScale = 1.0;

    var _lsmCachedResult = null;
    var _lsmCachedResultTime = 0;
    var _LSM_CACHE_TTL = 30000; // 30 seconds

    var _lsmCachedRunName = null;

    function _lsmFetchResults(callback) {
        var sel = document.getElementById('lsm-pp-run-select');
        var runName = sel ? sel.value : '';

        var now = Date.now();
        if (_lsmCachedResult && _lsmCachedRunName === runName && (now - _lsmCachedResultTime) < _LSM_CACHE_TTL) {
            callback(null, _lsmCachedResult);
            return;
        }
        var url = '/api/liquid/results';
        if (runName) url += '?run=' + encodeURIComponent(runName);
        var xhr = new XMLHttpRequest();
        xhr.open('GET', url, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.withCredentials = true;
        xhr.onload = function() {
            if (xhr.status === 200) {
                try {
                    var data = JSON.parse(xhr.responseText);
                    _lsmCachedResult = data;
                    _lsmCachedRunName = runName;
                    _lsmCachedResultTime = Date.now();
                    callback(null, data);
                } catch (e) {
                    callback('JSON parse error: ' + e.message);
                }
            } else {
                callback('HTTP ' + xhr.status + ': ' + xhr.responseText);
            }
        };
        xhr.onerror = function() { callback('Network error'); };
        xhr.send();
    }

    var _lsmColors = [
        '#e06c75','#e5c07b','#61afef','#c678dd','#56b6c2',
        '#98c379','#d19a66','#ff6b9d','#4ecdc4','#f7dc6f'
    ];

    var _lsmLossColors = {
        'total': '#ffffff',
        'mass': '#e06c75',
        'pipe_momentum': '#e5c07b',
        'pipe_energy': '#61afef',
        'pipe_ic': '#c678dd',
        'cold_plate_momentum': '#56b6c2',
        'cold_plate_energy': '#98c379',
        'cold_plate_ic': '#d19a66',
        'bc_P': '#ff6b9d',
        'dead_end_flow': '#4ecdc4'
    };

    // CP-CU list population
    window.lsmLoadCPList = function(graph) {
        var listEl = document.getElementById('lsm-cp-list');
        if (!listEl) return;
        var racks = graph.racks || [];
        if (racks.length === 0) {
            listEl.innerHTML = '<div style="font-size:10px; color:#666; padding:4px;">No CP-CU pairs in scene</div>';
            return;
        }
        listEl.innerHTML = '';
        racks.forEach(function(rack) {
            (rack.servers || []).forEach(function(srv) {
                (srv.cp_cu_pairs || []).forEach(function(cp) {
                    var row = document.createElement('div');
                    row.style.cssText = 'font-size:10px; padding:1px 0; color:#ccc;';
                    var qw = cp.Q_W != null ? cp.Q_W : '?';
                    row.innerHTML = '<label><input type="checkbox" class="lsm-cp-cb" ' +
                        'data-cp-id="' + cp.edge_id + '" ' +
                        'data-label="' + rack.id + '/' + srv.id + '/' + cp.id + '" ' +
                        'checked> ' +
                        rack.id + '/' + srv.id + '/' + cp.id + ' (' + qw + 'W)</label>';
                    listEl.appendChild(row);
                });
            });
        });
    };

    window.lsmToggleAllCP = function(checked) {
        var cbs = document.querySelectorAll('.lsm-cp-cb');
        for (var i = 0; i < cbs.length; i++) cbs[i].checked = checked;
    };

    window.lsmGetMonitoringConfig = function() {
        var lossCb = document.getElementById('lsm-loss-cb');
        var cpCbs = document.querySelectorAll('.lsm-cp-cb:checked');
        var cpList = [];
        for (var i = 0; i < cpCbs.length; i++) {
            cpList.push({
                id: cpCbs[i].getAttribute('data-cp-id'),
                label: cpCbs[i].getAttribute('data-label')
            });
        }
        var cduCbs = document.querySelectorAll('.lsm-cdu-cb:checked');
        var cduMetrics = [];
        for (var j = 0; j < cduCbs.length; j++) {
            cduMetrics.push(cduCbs[j].getAttribute('data-metric'));
        }
        return {
            loss: lossCb ? lossCb.checked : true,
            cpList: cpList,
            cduMetrics: cduMetrics
        };
    };

    window._lsmPopulateCPListFromState = function() {
        if (!window.state || typeof window.allModels !== 'function') return;
        var racks = [];
        window.allModels().forEach(function(m) {
            if (m.kind !== 'rack') return;
            var rackEntry = { id: m.meta.name || m.uid, servers: [] };
            Object.values((m.constructions && m.constructions.servers) || {}).forEach(function(eq) {
                if (!eq.liquidCooling || !eq.liquidCooling.cpCuPairs || eq.liquidCooling.cpCuPairs.length === 0) return;
                var srvEntry = { id: (eq.meta && eq.meta.name) || eq.uid, cp_cu_pairs: [] };
                eq.liquidCooling.cpCuPairs.forEach(function(cp) {
                    srvEntry.cp_cu_pairs.push({
                        id: cp.name || ('CP-' + srvEntry.cp_cu_pairs.length),
                        edge_id: cp.edge_id || (rackEntry.id + '/' + srvEntry.id + '/' + (cp.name || ('CP-' + srvEntry.cp_cu_pairs.length))),
                        Q_W: cp.Q_W != null ? cp.Q_W : (cp.heatLoad != null ? cp.heatLoad : 0)
                    });
                });
                rackEntry.servers.push(srvEntry);
            });
            if (rackEntry.servers.length > 0) racks.push(rackEntry);
        });
        window.lsmLoadCPList({ racks: racks });
    };

    window._lsmShowPostProcessHint = function() {
        var ppRight = document.getElementById('liquid-postprocess-right');
        if (ppRight) ppRight.style.display = 'flex';
        _lsmPopulatePPRunSelect();
    };

    function _lsmPopulatePPRunSelect() {
        var sel = document.getElementById('lsm-pp-run-select');
        if (!sel) return;
        var runs = (window.state && window.uiState.session && window.uiState.session.liquidRuns) || [];
        sel.innerHTML = '';
        var hasResults = window.state && window.state.liquidResults &&
                         window.state.liquidResults.rack_summaries;
        if (runs.length === 0 && hasResults) {
            var opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Latest';
            sel.appendChild(opt);
            sel.disabled = false;
            var refreshBtn = sel.nextElementSibling;
            if (refreshBtn && refreshBtn.tagName === 'BUTTON') refreshBtn.disabled = false;
            return;
        }
        if (runs.length === 0) {
            var opt = document.createElement('option');
            opt.value = '';
            opt.textContent = t('label.no_solve_records');
            sel.appendChild(opt);
            return;
        }
        runs.forEach(function(run) {
            var opt = document.createElement('option');
            opt.value = run.path || run.name;
            opt.textContent = run.name;
            sel.appendChild(opt);
        });
        sel.disabled = false;
        sel.selectedIndex = 0;
    }

    window._lsmRefreshPPRuns = function() {
        _lsmCachedResult = null;
        _lsmCachedResultTime = 0;
        _lsmCachedRunName = null;
        if (typeof window.scanCaseStatus === 'function') {
            window.scanCaseStatus().then(function() {
                _lsmPopulatePPRunSelect();
            });
        }
    };

    window._lsmOnPPRunChange = function() {
        _lsmCachedResult = null;
        _lsmCachedResultTime = 0;
        _lsmCachedRunName = null;
    };

    // Monitoring lifecycle
    window._lsmStartMonitoring = function(monCfg) {
        window._liquidSolverMonitor = {
            active: true,
            losses: {},
            temperatures: {},
            flows: {},
            maxEpochs: 10000
        };
        window._lsmHiddenSeries = {};
        window._lsmActiveTab = 'loss';
        _lsmLastLoggedEpoch = -Infinity;
        _lsmCreateMonitorWindow();
    };

    // SSE data handler
    var _lsmLogInterval = 50;
    var _lsmLastLoggedEpoch = -Infinity;

    window._lsmOnSSE = function(payload) {
        var mon = window._liquidSolverMonitor;
        if (!mon) return;

        // Stage transitions — log to Operation Log and update header badge
        if (payload.stage && payload.stage !== 'epoch') {
            _lsmUpdateStageBadge(payload.stage);
            if (payload.stage === 'compiling') {
                logOperation('liquidSolve', t('lsm.stage_compiling'), {}, 'modify');
                return;
            }
            if (payload.stage === 'compiled' || payload.stage === 'validated') {
                logOperation('liquidSolve', t('lsm.stage_compiled'), {}, 'modify');
                return;
            }
            if (payload.stage === 'model_built') {
                logOperation('liquidSolve', t('lsm.stage_model_built'), {}, 'modify');
                return;
            }
            if (payload.stage === 'torch_compiled') {
                logOperation('liquidSolve', 'torch.compile (' + (payload.mode || 'default') + ')', {}, 'modify');
                return;
            }
            if (payload.stage === 'solving') {
                mon.maxEpochs = payload.max_epochs || 10000;
                logOperation('liquidSolve', t('lsm.stage_solving') + ' (max ' + mon.maxEpochs + ' epochs)', {}, 'modify');
                return;
            }
            if (payload.stage === 'done') {
                mon.active = false;
                _lsmDrawChart();
                logOperation('liquidSolve', t('lsm.stage_done'), {}, 'modify');
                return;
            }
            if (payload.stage === 'error') {
                mon.active = false;
                _lsmDrawChart();
                var errMsg = payload.message || 'unknown error';
                logOperation('liquidSolve', t('lsm.stage_error') + ': ' + errMsg, {}, 'modify');
                return;
            }
        }

        if (payload.epoch == null) return;

        var epoch = payload.epoch;
        var losses = payload.losses || {};
        var total = payload.total;

        if (total != null) {
            if (!mon.losses['total']) mon.losses['total'] = [];
            mon.losses['total'].push({ epoch: epoch, value: total });
        }
        for (var k in losses) {
            if (!mon.losses[k]) mon.losses[k] = [];
            mon.losses[k].push({ epoch: epoch, value: losses[k] });
        }

        var cpMon = payload.cp_monitor;
        if (cpMon) {
            for (var cpId in cpMon) {
                var d = cpMon[cpId];
                var chipKey = cpId + ' T_chip';
                var waterKey = cpId + ' T_out';
                var flowKey = cpId + ' m';

                if (!mon.temperatures[chipKey]) mon.temperatures[chipKey] = [];
                mon.temperatures[chipKey].push({ epoch: epoch, value: d.T_chip });

                if (!mon.temperatures[waterKey]) mon.temperatures[waterKey] = [];
                mon.temperatures[waterKey].push({ epoch: epoch, value: d.T_water_out });

                if (!mon.flows[flowKey]) mon.flows[flowKey] = [];
                mon.flows[flowKey].push({ epoch: epoch, value: d.m });
            }
        }

        var cduMon = payload.cdu_monitor;
        if (cduMon) {
            if (cduMon.total_flow_kg_s != null) {
                var tfKey = 'CDU total_flow';
                if (!mon.flows[tfKey]) mon.flows[tfKey] = [];
                mon.flows[tfKey].push({ epoch: epoch, value: cduMon.total_flow_kg_s });
            }
            if (cduMon.supply_T_K != null) {
                var stKey = 'CDU supply_T';
                if (!mon.temperatures[stKey]) mon.temperatures[stKey] = [];
                mon.temperatures[stKey].push({ epoch: epoch, value: cduMon.supply_T_K });
            }
        }

        // Throttled epoch log to Operation Log
        if (epoch - _lsmLastLoggedEpoch >= _lsmLogInterval) {
            _lsmLastLoggedEpoch = epoch;
            var maxEp = mon.maxEpochs || '?';
            var summary = 'Epoch ' + epoch + '/' + maxEp;
            if (total != null) summary += '  loss=' + total.toExponential(2);
            if (losses.mass != null) summary += '  mass=' + losses.mass.toExponential(1);
            if (losses.energy != null) summary += '  energy=' + losses.energy.toExponential(1);
            // Show max chip temperature if available
            var maxChip = _lsmGetMaxChipTemp(cpMon);
            if (maxChip != null) summary += '  T_chip_max=' + maxChip.toFixed(1) + '°C';
            logOperation('liquidSolve', summary, {epoch: epoch, total: total}, 'modify');
        }

        _lsmDrawChart();
    };

    function _lsmGetMaxChipTemp(cpMon) {
        if (!cpMon) return null;
        var max = -Infinity;
        for (var cpId in cpMon) {
            var tk = cpMon[cpId].T_chip;
            if (tk != null) {
                var celsius = tk > 200 ? tk - 273.15 : tk;
                if (celsius > max) max = celsius;
            }
        }
        return max === -Infinity ? null : max;
    }

    function _lsmUpdateStageBadge(stage) {
        var badge = document.getElementById('lsm-stage-badge');
        if (!badge) return;
        var labels = {
            compiling: '⚙ Compiling',
            compiled: '⚙ Compiled',
            validated: '✓ Validated',
            model_built: '✓ Model Built',
            torch_compiled: '⚡ torch.compile',
            solving: '▶ Solving',
            done: '✔ Done',
            error: '✖ Error'
        };
        var colors = {
            compiling: '#e5c07b',
            compiled: '#e5c07b',
            validated: '#98c379',
            model_built: '#98c379',
            torch_compiled: '#d19a66',
            solving: '#61afef',
            done: '#98c379',
            error: '#e06c75'
        };
        badge.textContent = labels[stage] || stage;
        badge.style.color = colors[stage] || '#ccc';
    }

    // Cleanup refs for event listeners and ResizeObserver
    var _lsmDragMoveHandler = null;
    var _lsmDragUpHandler = null;
    var _lsmResizeObserver = null;

    function _lsmCleanupListeners() {
        if (_lsmDragMoveHandler) { document.removeEventListener('mousemove', _lsmDragMoveHandler); _lsmDragMoveHandler = null; }
        if (_lsmDragUpHandler) { document.removeEventListener('mouseup', _lsmDragUpHandler); _lsmDragUpHandler = null; }
        if (_lsmResizeObserver) { _lsmResizeObserver.disconnect(); _lsmResizeObserver = null; }
    }

    // Monitor window creation (matches air-mode _smCreateMonitorWindow pattern)
    function _lsmCreateMonitorWindow() {
        _lsmCleanupListeners();
        var existing = document.getElementById('lsm-monitor-window');
        if (existing) existing.remove();

        var win = document.createElement('div');
        win.id = 'lsm-monitor-window';
        win.style.cssText = 'position:fixed; z-index:900; top:60px; right:20px; width:640px; height:400px; ' +
            'min-width:420px; min-height:220px; ' +
            'display:flex; flex-direction:column; ' +
            'background:#1a1a2e; border:1px solid #555; border-radius:6px; ' +
            'box-shadow:0 4px 20px rgba(0,0,0,0.6); font-family:"Share Tech Mono",monospace; font-size:12px; color:#ccc;';
        win._lsmExpandedHeight = '400px';

        // Header
        var header = document.createElement('div');
        header.style.cssText = 'display:flex; align-items:center; justify-content:space-between; ' +
            'padding:6px 10px; background:#222238; border-bottom:1px solid #444; border-radius:6px 6px 0 0; cursor:move; user-select:none; flex-shrink:0;';
        header.innerHTML =
            '<span id="lsm-collapse-btn" style="cursor:pointer; color:#888; font-size:14px; margin-right:8px;">▼</span>' +
            '<span style="flex:1; font-weight:bold; color:#00b4dc; font-size:11px;">' + t('lsm.window_title') + '</span>' +
            '<span id="lsm-stage-badge" style="font-size:10px; color:#e5c07b; margin-right:10px; font-weight:bold;"></span>' +
            '<span id="lsm-font-down" title="Font −" style="cursor:pointer; color:#888; font-size:14px; margin-right:4px; padding:0 4px; border:1px solid #555; border-radius:3px;">A−</span>' +
            '<span id="lsm-font-up" title="Font +" style="cursor:pointer; color:#888; font-size:14px; margin-right:8px; padding:0 4px; border:1px solid #555; border-radius:3px;">A+</span>' +
            '<span id="lsm-close-btn" style="cursor:pointer; color:#e06c75; font-size:16px;">&times;</span>';
        win.appendChild(header);

        // Body (collapsible)
        var body = document.createElement('div');
        body.id = 'lsm-monitor-body';
        body.style.cssText = 'display:flex; flex-direction:column; flex:1; min-height:0; overflow:hidden;';

        // Tab bar
        var tabBar = document.createElement('div');
        tabBar.style.cssText = 'display:flex; border-bottom:1px solid #333; flex-shrink:0;';
        var tabs = [
            { id: 'loss', label: 'Loss' },
            { id: 'temperature', label: t('lsm.tab_temperature') },
            { id: 'flow', label: t('lsm.tab_flow') }
        ];
        tabs.forEach(function(tab) {
            var btn = document.createElement('button');
            btn.id = 'lsm-tab-' + tab.id;
            btn.textContent = tab.label;
            btn.style.cssText = 'flex:1; padding:4px 0; font-size:11px; border:none; cursor:pointer; ' +
                (tab.id === 'loss' ? 'background:#1a1a2e; color:#00b4dc; border-bottom:2px solid #00b4dc;' :
                                      'background:#111; color:#666; border-bottom:2px solid transparent;');
            btn.addEventListener('click', function() {
                window._lsmActiveTab = tab.id;
                tabs.forEach(function(t2) {
                    var b = document.getElementById('lsm-tab-' + t2.id);
                    if (b) {
                        b.style.background = t2.id === tab.id ? '#1a1a2e' : '#111';
                        b.style.color = t2.id === tab.id ? '#00b4dc' : '#666';
                        b.style.borderBottom = t2.id === tab.id ? '2px solid #00b4dc' : '2px solid transparent';
                    }
                });
                _lsmDrawChart();
            });
            tabBar.appendChild(btn);
        });
        body.appendChild(tabBar);

        var content = document.createElement('div');
        content.style.cssText = 'flex:1; min-height:0; display:flex; gap:6px; padding:6px; overflow:hidden;';

        var canvasWrap = document.createElement('div');
        canvasWrap.id = 'lsm-canvas-wrap';
        canvasWrap.style.cssText = 'flex:1; min-width:0; min-height:120px; position:relative;';

        var canvas = document.createElement('canvas');
        canvas.id = 'lsm-chart-canvas';
        canvas.style.cssText = 'background:#0d0d1a; border:1px solid #333; border-radius:3px; display:block; width:100%; height:100%;';
        canvasWrap.appendChild(canvas);

        var legend = document.createElement('div');
        legend.id = 'lsm-chart-legend';
        legend.style.cssText = 'width:130px; flex-shrink:0; overflow-y:auto; padding:4px; margin-left:6px; ' +
            'font-family:monospace; font-size:10px; display:flex; flex-direction:column; gap:2px;';

        content.appendChild(canvasWrap);
        content.appendChild(legend);
        body.appendChild(content);

        // Edge + corner resize handles
        var handles = [
            { cursor: 'e-resize',  pos: 'top:0;right:-4px;width:8px;height:100%;' },
            { cursor: 'w-resize',  pos: 'top:0;left:-4px;width:8px;height:100%;' },
            { cursor: 's-resize',  pos: 'bottom:-4px;left:0;width:100%;height:8px;' },
            { cursor: 'n-resize',  pos: 'top:-4px;left:0;width:100%;height:8px;' },
            { cursor: 'se-resize', pos: 'bottom:-4px;right:-4px;width:12px;height:12px;' },
            { cursor: 'sw-resize', pos: 'bottom:-4px;left:-4px;width:12px;height:12px;' },
            { cursor: 'ne-resize', pos: 'top:-4px;right:-4px;width:12px;height:12px;' },
            { cursor: 'nw-resize', pos: 'top:-4px;left:-4px;width:12px;height:12px;' }
        ];
        handles.forEach(function(h) {
            var el = document.createElement('div');
            el.style.cssText = 'position:absolute;z-index:10;cursor:' + h.cursor + ';' + h.pos;
            el.setAttribute('data-resize', h.cursor);
            win.appendChild(el);
        });

        win.appendChild(body);
        document.body.appendChild(win);

        // Collapse toggle
        var _lsmSavedHeight = win.style.height;
        document.getElementById('lsm-collapse-btn').addEventListener('click', function() {
            var b = document.getElementById('lsm-monitor-body');
            if (b.style.display === 'none') {
                b.style.display = 'flex';
                win.style.height = _lsmSavedHeight || '400px';
                win._lsmExpandedHeight = win.style.height;
                win.style.minHeight = '220px';
                this.textContent = '▼';
                requestAnimationFrame(function() { _lsmResizeCanvas(); });
            } else {
                _lsmSavedHeight = win.style.height;
                win._lsmExpandedHeight = _lsmSavedHeight;
                b.style.display = 'none';
                win.style.height = 'auto';
                win.style.minHeight = '0';
                this.textContent = '▶';
            }
        });

        // Close
        document.getElementById('lsm-close-btn').addEventListener('click', function() {
            _lsmCleanupListeners();
            var w = document.getElementById('lsm-monitor-window');
            if (w) w.remove();
        });

        // Font size
        document.getElementById('lsm-font-up').addEventListener('click', function(e) {
            e.stopPropagation();
            window._lsmFontScale = Math.min(2, window._lsmFontScale + 0.1);
            _lsmDrawChart();
        });
        document.getElementById('lsm-font-down').addEventListener('click', function(e) {
            e.stopPropagation();
            window._lsmFontScale = Math.max(0.6, window._lsmFontScale - 0.1);
            _lsmDrawChart();
        });

        // Drag + Resize system
        var isDragging = false, isResizing = false, resizeDir = '';
        var dragOffX = 0, dragOffY = 0;
        var startRect = null, startX = 0, startY = 0;

        win.addEventListener('mousedown', function(e) {
            var dir = e.target.getAttribute('data-resize');
            if (dir) {
                isResizing = true;
                resizeDir = dir.split('-')[0];
                startRect = win.getBoundingClientRect();
                startX = e.clientX; startY = e.clientY;
                e.preventDefault(); e.stopPropagation();
                return;
            }
            var tag = e.target.tagName.toLowerCase();
            if (tag === 'button' || tag === 'input' || tag === 'select') return;
            if (e.target.id === 'lsm-collapse-btn' || e.target.id === 'lsm-close-btn' ||
                e.target.id === 'lsm-font-up' || e.target.id === 'lsm-font-down') return;
            if (e.target.closest && e.target.closest('#lsm-chart-legend')) return;
            isDragging = true;
            var rect = win.getBoundingClientRect();
            dragOffX = e.clientX - rect.left; dragOffY = e.clientY - rect.top;
            e.preventDefault();
        });

        _lsmDragMoveHandler = function(e) {
            if (isDragging) {
                win.style.left = (e.clientX - dragOffX) + 'px';
                win.style.top = (e.clientY - dragOffY) + 'px';
                win.style.right = 'auto';
            } else if (isResizing) {
                var dx = e.clientX - startX, dy = e.clientY - startY;
                var r = startRect;
                var newLeft = r.left, newTop = r.top, newW = r.width, newH = r.height;
                var d = resizeDir;
                if (d === 'e' || d === 'se' || d === 'ne') newW = Math.max(420, r.width + dx);
                if (d === 'w' || d === 'sw' || d === 'nw') { newW = Math.max(420, r.width - dx); newLeft = r.right - newW; }
                if (d === 's' || d === 'se' || d === 'sw') newH = Math.max(220, r.height + dy);
                if (d === 'n' || d === 'ne' || d === 'nw') { newH = Math.max(220, r.height - dy); newTop = r.bottom - newH; }
                win.style.left = newLeft + 'px'; win.style.top = newTop + 'px';
                win.style.right = 'auto';
                win.style.width = newW + 'px'; win.style.height = newH + 'px';
                win._lsmExpandedHeight = win.style.height;
                _lsmResizeCanvas();
            }
        };
        _lsmDragUpHandler = function() {
            if (isResizing) { isResizing = false; _lsmDrawChart(); }
            isDragging = false;
        };
        document.addEventListener('mousemove', _lsmDragMoveHandler);
        document.addEventListener('mouseup', _lsmDragUpHandler);

        _lsmResizeObserver = new ResizeObserver(function() { _lsmResizeCanvas(); });
        _lsmResizeObserver.observe(canvasWrap);
        requestAnimationFrame(function() { _lsmResizeCanvas(); });
    }

    function _lsmResizeCanvas() {
        var canvas = document.getElementById('lsm-chart-canvas');
        var wrap = document.getElementById('lsm-canvas-wrap');
        if (!canvas || !wrap) return;
        var dpr = window.devicePixelRatio || 1;
        var w = wrap.clientWidth;
        var h = wrap.clientHeight;
        if (w < 10 || h < 10) return;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        _lsmDrawChart();
    }
    window._lsmResizeCanvas = _lsmResizeCanvas;

    // Chart drawing
    function _lsmDrawChart() {
        var canvas = document.getElementById('lsm-chart-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var dpr = window.devicePixelRatio || 1;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var lW = W / dpr, lH = H / dpr;

        var fs = window._lsmFontScale || 1.0;
        var mon = window._liquidSolverMonitor;
        if (!mon) return;

        var activeTab = window._lsmActiveTab;
        var isLog = (activeTab === 'loss');
        var isTempTab = (activeTab === 'temperature');
        var datasets, yLabel;
        if (activeTab === 'loss') { datasets = mon.losses; yLabel = 'log10(loss)'; }
        else if (isTempTab) { datasets = mon.temperatures; yLabel = '°C'; }
        else { datasets = mon.flows; yLabel = 'kg/s'; }

        var seriesNames = Object.keys(datasets || {});

        ctx.fillStyle = '#0d0d1a';
        ctx.fillRect(0, 0, lW, lH);

        if (seriesNames.length === 0) {
            ctx.fillStyle = '#666';
            ctx.font = Math.round(12 * fs) + 'px monospace';
            ctx.fillText('Waiting for data...', lW / 2 - 60, lH / 2);
            _lsmUpdateLegend(seriesNames, isLog);
            return;
        }

        var labelFs = Math.round(9 * fs);
        var ml = Math.max(55, 50 * fs + 5), mr = 10, mt = 10, mb = Math.max(25, 20 * fs + 5);
        var cw = lW - ml - mr, ch = lH - mt - mb;
        if (cw < 20 || ch < 20) return;

        var xMin = Infinity, xMax = -Infinity;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._lsmHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].epoch < xMin) xMin = arr[j].epoch;
                if (arr[j].epoch > xMax) xMax = arr[j].epoch;
            }
        }
        if (xMin === Infinity) { xMin = 0; xMax = 100; }
        if (xMax <= xMin) xMax = xMin + 100;

        var yMin, yMax;
        if (isLog) {
            yMin = -8; yMax = 6;
            for (var k = 0; k < seriesNames.length; k++) {
                if (window._lsmHiddenSeries[seriesNames[k]]) continue;
                var arr = datasets[seriesNames[k]];
                for (var j = 0; j < arr.length; j++) {
                    var lv = Math.log10(Math.max(arr[j].value, 1e-15));
                    if (lv < yMin) yMin = lv;
                    if (lv > yMax) yMax = lv;
                }
            }
            yMin = Math.floor(yMin) - 1;
            yMax = Math.ceil(yMax) + 1;
        } else {
            yMin = Infinity; yMax = -Infinity;
            for (var k = 0; k < seriesNames.length; k++) {
                if (window._lsmHiddenSeries[seriesNames[k]]) continue;
                var arr = datasets[seriesNames[k]];
                for (var j = 0; j < arr.length; j++) {
                    var val = isTempTab ? arr[j].value - 273.15 : arr[j].value;
                    if (val < yMin) yMin = val;
                    if (val > yMax) yMax = val;
                }
            }
            if (yMin === Infinity) { yMin = 0; yMax = 100; }
            var padding = Math.max((yMax - yMin) * 0.1, 0.5);
            yMin -= padding; yMax += padding;
        }

        // Grid
        ctx.strokeStyle = '#222'; ctx.lineWidth = 0.5;
        ctx.font = labelFs + 'px monospace'; ctx.fillStyle = '#555';

        var ySteps = isLog ? Math.min(yMax - yMin, 14) : 5;
        for (var i = 0; i <= ySteps; i++) {
            var yVal = isLog ? (yMin + i) : (yMin + i * (yMax - yMin) / 5);
            var py = mt + ch - (yVal - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            var label = isLog ? ('1e' + Math.round(yVal)) : yVal.toFixed(1);
            ctx.fillText(label, 4, py + 3);
        }

        var xSteps = 5;
        for (var i = 0; i <= xSteps; i++) {
            var xVal = xMin + i * (xMax - xMin) / xSteps;
            var px = ml + (xVal - xMin) / (xMax - xMin) * cw;
            ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ch); ctx.stroke();
            ctx.fillText(Math.round(xVal).toString(), px - 10, mt + ch + 14);
        }

        ctx.save();
        ctx.translate(12, mt + ch / 2); ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = '#888'; ctx.fillText(yLabel, -20, 0);
        ctx.restore();
        ctx.fillStyle = '#888'; ctx.fillText('Epoch', ml + cw / 2 - 15, lH - 2);

        ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
        ctx.strokeRect(ml, mt, cw, ch);

        for (var k = 0; k < seriesNames.length; k++) {
            var name = seriesNames[k];
            if (window._lsmHiddenSeries[name]) continue;
            var arr = datasets[name];
            if (arr.length < 2) continue;
            var color = isLog ? (_lsmLossColors[name] || _lsmColors[k % _lsmColors.length]) : _lsmColors[k % _lsmColors.length];
            ctx.strokeStyle = color;
            ctx.lineWidth = name === 'total' ? 2 : 1;
            ctx.beginPath();
            for (var j = 0; j < arr.length; j++) {
                var px = ml + (arr[j].epoch - xMin) / (xMax - xMin) * cw;
                var rawVal = isLog ? Math.log10(Math.max(arr[j].value, 1e-15)) :
                             (isTempTab ? arr[j].value - 273.15 : arr[j].value);
                var py = mt + ch - (rawVal - yMin) / (yMax - yMin) * ch;
                if (j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            }
            ctx.stroke();
        }

        _lsmUpdateLegend(seriesNames, isLog);
    }
    window._lsmDrawChart = _lsmDrawChart;

    // Legend
    function _lsmUpdateLegend(seriesNames, isLog) {
        var legend = document.getElementById('lsm-chart-legend');
        if (!legend) return;
        var fs = window._lsmFontScale || 1.0;
        legend.style.fontSize = Math.round(10 * fs) + 'px';
        legend.innerHTML = '';

        if (seriesNames.length === 0) {
            var empty = document.createElement('div');
            empty.style.cssText = 'color:#666; line-height:1.4;';
            empty.textContent = 'Waiting...';
            legend.appendChild(empty);
            return;
        }

        for (var i = 0; i < seriesNames.length; i++) {
            var name = seriesNames[i];
            var color = isLog ? (_lsmLossColors[name] || _lsmColors[i % _lsmColors.length]) : _lsmColors[i % _lsmColors.length];
            var hidden = !!window._lsmHiddenSeries[name];
            var item = document.createElement('div');
            item.style.cssText = 'cursor:pointer; display:flex; align-items:flex-start; gap:6px; ' +
                'padding:3px 4px; border:1px solid #2f3448; border-radius:3px; background:#151a28;' +
                (hidden ? ' opacity:0.3;' : '');
            item.innerHTML = '<span style="display:inline-block; width:10px; height:3px; margin-top:6px; flex-shrink:0; background:' + color + '; border-radius:999px;"></span>' +
                '<span style="min-width:0; overflow-wrap:anywhere; word-break:break-word; line-height:1.3; font-size:9px;">' + name + '</span>';
            item.setAttribute('data-series', name);
            item.addEventListener('click', function() {
                var s = this.getAttribute('data-series');
                window._lsmHiddenSeries[s] = !window._lsmHiddenSeries[s];
                _lsmDrawChart();
            });
            legend.appendChild(item);
        }
    }

    // ── Post-processing floating window ──────────────────────────

    window.lsmShowPostProcessWindow = function(objectType, objectId, objectLabel) {
        var statusEl = document.getElementById('lsm-pp-status');
        if (statusEl) {
            statusEl.style.display = '';
            statusEl.textContent = t('lsm.pp_loading');
        }

        _lsmFetchResults(function(err, data) {
            if (statusEl) statusEl.style.display = 'none';
            if (err) {
                alert('Failed to load results: ' + err);
                return;
            }
            _lsmShowPPWindow(objectType, objectId, objectLabel, data);
        });
    };

    function _lsmShowPPWindow(objectType, objectId, label, result) {
        var time_s = result.time_s || [];
        var datasets = { temperature: {}, flow: {}, pressure: {} };

        if (objectType === 'cp') {
            // Try new cp_details first (has per-CP inlet temp + pressures)
            var cpDetail = null;
            if (result.cp_details) {
                for (var di = 0; di < result.cp_details.length; di++) {
                    if (result.cp_details[di].edge_id === objectId) {
                        cpDetail = result.cp_details[di];
                        break;
                    }
                }
            }

            if (cpDetail) {
                // New path: 3 temperature lines + 2 pressure lines
                if (cpDetail.water_inlet_temp_K) {
                    datasets.temperature['T_inlet'] = _lsmBuildTimeSeries(time_s, cpDetail.water_inlet_temp_K);
                }
                if (cpDetail.chip_temp_K) {
                    datasets.temperature['T_chip'] = _lsmBuildTimeSeries(time_s, cpDetail.chip_temp_K);
                }
                if (cpDetail.water_outlet_temp_K) {
                    datasets.temperature['T_outlet'] = _lsmBuildTimeSeries(time_s, cpDetail.water_outlet_temp_K);
                }
                if (cpDetail.inlet_pressure_Pa) {
                    datasets.pressure['P_inlet'] = _lsmBuildTimeSeries(time_s, cpDetail.inlet_pressure_Pa);
                }
                if (cpDetail.outlet_pressure_Pa) {
                    datasets.pressure['P_outlet'] = _lsmBuildTimeSeries(time_s, cpDetail.outlet_pressure_Pa);
                }
            } else {
                // Fallback: old result format (chip_temp_K / water_outlet_temp_K flat arrays)
                var cpIdx = -1;
                var cpEdgeOrder = [];
                (result.rack_summaries || []).forEach(function(rack) {
                    (rack.servers || []).forEach(function(srv) {
                        (srv.cp_edge_ids || []).forEach(function(eid) {
                            cpEdgeOrder.push(eid);
                        });
                    });
                });
                cpIdx = cpEdgeOrder.indexOf(objectId);

                if (cpIdx >= 0 && result.chip_temp_K && result.chip_temp_K[cpIdx]) {
                    datasets.temperature['T_chip'] = _lsmBuildTimeSeries(time_s, result.chip_temp_K[cpIdx]);
                    var waterTemps = (result.water_outlet_temp_K || [])[cpIdx] || [];
                    if (waterTemps.length > 0) {
                        datasets.temperature['T_outlet'] = _lsmBuildTimeSeries(time_s, waterTemps);
                    }
                }
                // Old format lacks per-CP pressures; show system BC pressures instead
                _lsmAddBCPressure(datasets.pressure, result, time_s);
            }

            var edgeIdx = (result.edge_ids || []).indexOf(objectId);
            if (edgeIdx >= 0 && result.edge_flow_kg_s && result.edge_flow_kg_s[edgeIdx]) {
                datasets.flow['m'] = _lsmBuildTimeSeries(time_s, result.edge_flow_kg_s[edgeIdx]);
            }

        } else if (objectType === 'cdu') {
            (result.rack_summaries || []).forEach(function(rack) {
                if (rack.supply_temp_K) {
                    datasets.temperature['Supply T (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.supply_temp_K);
                }
                if (rack.return_temp_K) {
                    datasets.temperature['Return T (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.return_temp_K);
                }
                if (rack.total_flow_kg_s) {
                    datasets.flow['Flow (' + rack.rack_id + ')'] = _lsmBuildTimeSeries(time_s, rack.total_flow_kg_s);
                }
            });
            _lsmAddBCPressure(datasets.pressure, result, time_s);
        }

        _lsmCreatePostProcessWindow(label || objectId, datasets);
    }

    function _lsmBuildTimeSeries(time_s, values) {
        var arr = [];
        var len = Math.min(time_s.length, values.length);
        for (var i = 0; i < len; i++) {
            arr.push({ epoch: time_s[i], value: values[i] });
        }
        return arr;
    }

    function _lsmAddBCPressure(pressureDatasets, result, time_s) {
        if (!result.node_pressure_Pa || !result.node_ids) return;
        for (var i = 0; i < result.node_ids.length; i++) {
            var nid = result.node_ids[i];
            if (nid.indexOf('supply') >= 0 || nid.indexOf('return') >= 0 ||
                nid.indexOf('_bc') >= 0 || nid.indexOf('BC') >= 0) {
                var pArr = result.node_pressure_Pa[i];
                if (pArr) {
                    pressureDatasets[nid] = _lsmBuildTimeSeries(time_s, pArr);
                }
            }
        }
    }

    var _lsmPPActiveTab = 'temperature';

    function _lsmCreatePostProcessWindow(title, datasets) {
        _lsmCleanupListeners();
        var existing = document.getElementById('lsm-monitor-window');
        if (existing) existing.remove();

        window._lsmPPDatasets = datasets;
        _lsmPPActiveTab = 'temperature';

        var win = document.createElement('div');
        win.id = 'lsm-monitor-window';
        win.style.cssText = 'position:fixed; z-index:900; top:60px; right:20px; width:640px; height:400px; ' +
            'min-width:420px; min-height:220px; ' +
            'display:flex; flex-direction:column; ' +
            'background:#1a1a2e; border:1px solid #555; border-radius:6px; ' +
            'box-shadow:0 4px 20px rgba(0,0,0,0.6); font-family:"Share Tech Mono",monospace; font-size:12px; color:#ccc;';
        win._lsmExpandedHeight = '400px';

        // Header
        var header = document.createElement('div');
        header.style.cssText = 'display:flex; align-items:center; justify-content:space-between; ' +
            'padding:6px 10px; background:#222238; border-bottom:1px solid #444; border-radius:6px 6px 0 0; cursor:move; user-select:none; flex-shrink:0;';
        header.innerHTML =
            '<span id="lsm-pp-collapse-btn" style="cursor:pointer; color:#888; font-size:14px; margin-right:8px;">▼</span>' +
            '<span style="flex:1; font-weight:bold; color:#00b4dc; font-size:11px;">📊 ' + title + '</span>' +
            '<span id="lsm-pp-font-down" title="Font −" style="cursor:pointer; color:#888; font-size:14px; margin-right:4px; padding:0 4px; border:1px solid #555; border-radius:3px;">A−</span>' +
            '<span id="lsm-pp-font-up" title="Font +" style="cursor:pointer; color:#888; font-size:14px; margin-right:8px; padding:0 4px; border:1px solid #555; border-radius:3px;">A+</span>' +
            '<span id="lsm-pp-close" style="cursor:pointer; color:#e06c75; font-size:16px;">&times;</span>';
        win.appendChild(header);

        // Body (collapsible)
        var body = document.createElement('div');
        body.id = 'lsm-monitor-body';
        body.style.cssText = 'display:flex; flex-direction:column; flex:1; min-height:0; overflow:hidden;';

        // Tab bar: Temperature / Flow / Pressure
        var tabBar = document.createElement('div');
        tabBar.style.cssText = 'display:flex; border-bottom:1px solid #333; flex-shrink:0;';
        var ppTabs = [
            { id: 'temperature', label: '🌡 ' + t('lsm.tab_temperature') },
            { id: 'flow', label: '💧 ' + t('lsm.tab_flow') },
            { id: 'pressure', label: '📏 ' + t('lsm.tab_pressure') }
        ];
        ppTabs.forEach(function(tab) {
            var btn = document.createElement('button');
            btn.id = 'lsm-pp-tab-' + tab.id;
            btn.textContent = tab.label;
            btn.style.cssText = 'flex:1; padding:4px 0; font-size:11px; border:none; cursor:pointer; ' +
                (tab.id === 'temperature' ? 'background:#1a1a2e; color:#00b4dc; border-bottom:2px solid #00b4dc;' :
                                            'background:#111; color:#666; border-bottom:2px solid transparent;');
            btn.addEventListener('click', function() {
                _lsmPPActiveTab = tab.id;
                ppTabs.forEach(function(t2) {
                    var b = document.getElementById('lsm-pp-tab-' + t2.id);
                    if (b) {
                        b.style.background = t2.id === tab.id ? '#1a1a2e' : '#111';
                        b.style.color = t2.id === tab.id ? '#00b4dc' : '#666';
                        b.style.borderBottom = t2.id === tab.id ? '2px solid #00b4dc' : '2px solid transparent';
                    }
                });
                _lsmDrawPPChart();
            });
            tabBar.appendChild(btn);
        });
        body.appendChild(tabBar);

        var content = document.createElement('div');
        content.style.cssText = 'flex:1; min-height:0; display:flex; gap:6px; padding:6px; overflow:hidden;';

        var canvasWrap = document.createElement('div');
        canvasWrap.id = 'lsm-canvas-wrap';
        canvasWrap.style.cssText = 'flex:1; min-width:0; min-height:120px; position:relative;';

        var canvas = document.createElement('canvas');
        canvas.id = 'lsm-chart-canvas';
        canvas.style.cssText = 'background:#0d0d1a; border:1px solid #333; border-radius:3px; display:block; width:100%; height:100%;';
        canvasWrap.appendChild(canvas);

        var legend = document.createElement('div');
        legend.id = 'lsm-chart-legend';
        legend.style.cssText = 'width:130px; flex-shrink:0; overflow-y:auto; padding:4px; margin-left:6px; ' +
            'font-family:monospace; font-size:10px; display:flex; flex-direction:column; gap:2px;';

        content.appendChild(canvasWrap);
        content.appendChild(legend);
        body.appendChild(content);

        // Edge + corner resize handles
        var handles = [
            { cursor: 'e-resize',  pos: 'top:0;right:-4px;width:8px;height:100%;' },
            { cursor: 'w-resize',  pos: 'top:0;left:-4px;width:8px;height:100%;' },
            { cursor: 's-resize',  pos: 'bottom:-4px;left:0;width:100%;height:8px;' },
            { cursor: 'n-resize',  pos: 'top:-4px;left:0;width:100%;height:8px;' },
            { cursor: 'se-resize', pos: 'bottom:-4px;right:-4px;width:12px;height:12px;' },
            { cursor: 'sw-resize', pos: 'bottom:-4px;left:-4px;width:12px;height:12px;' },
            { cursor: 'ne-resize', pos: 'top:-4px;right:-4px;width:12px;height:12px;' },
            { cursor: 'nw-resize', pos: 'top:-4px;left:-4px;width:12px;height:12px;' }
        ];
        handles.forEach(function(h) {
            var el = document.createElement('div');
            el.style.cssText = 'position:absolute;z-index:10;cursor:' + h.cursor + ';' + h.pos;
            el.setAttribute('data-resize', h.cursor);
            win.appendChild(el);
        });

        win.appendChild(body);
        document.body.appendChild(win);

        // Collapse toggle
        var _ppSavedHeight = win.style.height;
        document.getElementById('lsm-pp-collapse-btn').addEventListener('click', function() {
            var b = document.getElementById('lsm-monitor-body');
            if (b.style.display === 'none') {
                b.style.display = 'flex';
                win.style.height = _ppSavedHeight || '400px';
                win._lsmExpandedHeight = win.style.height;
                win.style.minHeight = '220px';
                this.textContent = '▼';
                requestAnimationFrame(function() { _lsmResizePPCanvas(); });
            } else {
                _ppSavedHeight = win.style.height;
                win._lsmExpandedHeight = _ppSavedHeight;
                b.style.display = 'none';
                win.style.height = 'auto';
                win.style.minHeight = '0';
                this.textContent = '▶';
            }
        });

        // Close
        document.getElementById('lsm-pp-close').addEventListener('click', function() {
            _lsmCleanupListeners();
            var w = document.getElementById('lsm-monitor-window');
            if (w) w.remove();
        });

        // Font size
        document.getElementById('lsm-pp-font-up').addEventListener('click', function(e) {
            e.stopPropagation();
            window._lsmFontScale = Math.min(2, window._lsmFontScale + 0.1);
            _lsmDrawPPChart();
        });
        document.getElementById('lsm-pp-font-down').addEventListener('click', function(e) {
            e.stopPropagation();
            window._lsmFontScale = Math.max(0.6, window._lsmFontScale - 0.1);
            _lsmDrawPPChart();
        });

        // Drag + Resize system
        var isDragging = false, isResizing = false, resizeDir = '';
        var dragOffX = 0, dragOffY = 0;
        var startRect = null, startX = 0, startY = 0;

        win.addEventListener('mousedown', function(e) {
            var dir = e.target.getAttribute('data-resize');
            if (dir) {
                isResizing = true;
                resizeDir = dir.split('-')[0];
                startRect = win.getBoundingClientRect();
                startX = e.clientX; startY = e.clientY;
                e.preventDefault(); e.stopPropagation();
                return;
            }
            var tag = e.target.tagName.toLowerCase();
            if (tag === 'button' || tag === 'input' || tag === 'select' || tag === 'span') return;
            if (e.target.closest && e.target.closest('#lsm-chart-legend')) return;
            isDragging = true;
            var rect = win.getBoundingClientRect();
            dragOffX = e.clientX - rect.left; dragOffY = e.clientY - rect.top;
            e.preventDefault();
        });

        _lsmDragMoveHandler = function(e) {
            if (isDragging) {
                win.style.left = (e.clientX - dragOffX) + 'px';
                win.style.top = (e.clientY - dragOffY) + 'px';
                win.style.right = 'auto';
            } else if (isResizing) {
                var dx = e.clientX - startX, dy = e.clientY - startY;
                var r = startRect;
                var newLeft = r.left, newTop = r.top, newW = r.width, newH = r.height;
                var d = resizeDir;
                if (d === 'e' || d === 'se' || d === 'ne') newW = Math.max(420, r.width + dx);
                if (d === 'w' || d === 'sw' || d === 'nw') { newW = Math.max(420, r.width - dx); newLeft = r.right - newW; }
                if (d === 's' || d === 'se' || d === 'sw') newH = Math.max(220, r.height + dy);
                if (d === 'n' || d === 'ne' || d === 'nw') { newH = Math.max(220, r.height - dy); newTop = r.bottom - newH; }
                win.style.left = newLeft + 'px'; win.style.top = newTop + 'px';
                win.style.right = 'auto';
                win.style.width = newW + 'px'; win.style.height = newH + 'px';
                win._lsmExpandedHeight = win.style.height;
                _lsmResizePPCanvas();
            }
        };
        _lsmDragUpHandler = function() {
            if (isResizing) { isResizing = false; _lsmDrawPPChart(); }
            isDragging = false;
        };
        document.addEventListener('mousemove', _lsmDragMoveHandler);
        document.addEventListener('mouseup', _lsmDragUpHandler);

        _lsmResizeObserver = new ResizeObserver(function() { _lsmResizePPCanvas(); });
        _lsmResizeObserver.observe(canvasWrap);
        requestAnimationFrame(function() { _lsmResizePPCanvas(); });
    }

    function _lsmResizePPCanvas() {
        var canvas = document.getElementById('lsm-chart-canvas');
        var wrap = document.getElementById('lsm-canvas-wrap');
        if (!canvas || !wrap) return;
        var dpr = window.devicePixelRatio || 1;
        var w = wrap.clientWidth;
        var h = wrap.clientHeight;
        if (w < 10 || h < 10) return;
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        _lsmDrawPPChart();
    }

    function _lsmDrawPPChart() {
        var canvas = document.getElementById('lsm-chart-canvas');
        if (!canvas) return;
        var ctx = canvas.getContext('2d');
        var W = canvas.width, H = canvas.height;
        var dpr = window.devicePixelRatio || 1;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        var lW = W / dpr, lH = H / dpr;
        var fs = window._lsmFontScale || 1.0;

        var ppData = window._lsmPPDatasets;
        if (!ppData) return;

        var datasets = ppData[_lsmPPActiveTab] || {};
        var isPPTemp = (_lsmPPActiveTab === 'temperature');
        var isPPPressure = (_lsmPPActiveTab === 'pressure');
        var yLabel = isPPTemp ? '°C' :
                     _lsmPPActiveTab === 'flow' ? 'kg/s' : 'kPa';

        var seriesNames = Object.keys(datasets);

        ctx.fillStyle = '#0d0d1a';
        ctx.fillRect(0, 0, lW, lH);

        if (seriesNames.length === 0) {
            ctx.fillStyle = '#666';
            ctx.font = Math.round(12 * fs) + 'px monospace';
            ctx.fillText(t('lsm.pp_no_data_tab'), lW / 2 - 70, lH / 2);
            _lsmUpdatePPLegend(seriesNames);
            return;
        }

        var labelFs = Math.round(9 * fs);
        var ml = Math.max(65, 60 * fs + 5), mr = 10, mt = 10, mb = Math.max(25, 20 * fs + 5);
        var cw = lW - ml - mr, ch = lH - mt - mb;
        if (cw < 20 || ch < 20) return;

        // Compute ranges
        var xMin = Infinity, xMax = -Infinity;
        var yMin = Infinity, yMax = -Infinity;
        for (var k = 0; k < seriesNames.length; k++) {
            if (window._lsmHiddenSeries[seriesNames[k]]) continue;
            var arr = datasets[seriesNames[k]];
            for (var j = 0; j < arr.length; j++) {
                if (arr[j].epoch < xMin) xMin = arr[j].epoch;
                if (arr[j].epoch > xMax) xMax = arr[j].epoch;
                var yv = isPPTemp ? arr[j].value - 273.15 : isPPPressure ? arr[j].value / 1000.0 : arr[j].value;
                if (yv < yMin) yMin = yv;
                if (yv > yMax) yMax = yv;
            }
        }
        if (xMin === Infinity) { xMin = 0; xMax = 1; }
        if (xMax <= xMin) xMax = xMin + 1;
        if (yMin === Infinity) { yMin = 0; yMax = 1; }
        var yPad = Math.max((yMax - yMin) * 0.1, 0.5);
        yMin -= yPad; yMax += yPad;

        // Grid
        ctx.strokeStyle = '#222'; ctx.lineWidth = 0.5;
        ctx.font = labelFs + 'px monospace'; ctx.fillStyle = '#555';

        var ySteps = 5;
        for (var i = 0; i <= ySteps; i++) {
            var yVal = yMin + i * (yMax - yMin) / ySteps;
            var py = mt + ch - (yVal - yMin) / (yMax - yMin) * ch;
            ctx.beginPath(); ctx.moveTo(ml, py); ctx.lineTo(ml + cw, py); ctx.stroke();
            var label = _lsmFormatValue(yVal, _lsmPPActiveTab);
            ctx.fillText(label, 4, py + 3);
        }

        var xSteps = 5;
        for (var i = 0; i <= xSteps; i++) {
            var xVal = xMin + i * (xMax - xMin) / xSteps;
            var px = ml + (xVal - xMin) / (xMax - xMin) * cw;
            ctx.beginPath(); ctx.moveTo(px, mt); ctx.lineTo(px, mt + ch); ctx.stroke();
            ctx.fillText(xVal.toFixed(1) + 's', px - 12, mt + ch + 14);
        }

        // Axis labels
        ctx.save();
        ctx.translate(12, mt + ch / 2); ctx.rotate(-Math.PI / 2);
        ctx.fillStyle = '#888'; ctx.fillText(yLabel, -15, 0);
        ctx.restore();
        ctx.fillStyle = '#888'; ctx.fillText(t('lsm.pp_time_axis'), ml + cw / 2 - 20, lH - 2);

        // Border
        ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
        ctx.strokeRect(ml, mt, cw, ch);

        // Draw series
        for (var k = 0; k < seriesNames.length; k++) {
            var name = seriesNames[k];
            if (window._lsmHiddenSeries[name]) continue;
            var arr = datasets[name];
            if (arr.length < 2) continue;
            ctx.strokeStyle = _lsmColors[k % _lsmColors.length];
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            for (var j = 0; j < arr.length; j++) {
                var px = ml + (arr[j].epoch - xMin) / (xMax - xMin) * cw;
                var plotVal = isPPTemp ? arr[j].value - 273.15 : isPPPressure ? arr[j].value / 1000.0 : arr[j].value;
                var py = mt + ch - (plotVal - yMin) / (yMax - yMin) * ch;
                if (j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
            }
            ctx.stroke();
        }

        _lsmUpdatePPLegend(seriesNames);
    }

    function _lsmFormatValue(val, tab) {
        if (tab === 'pressure') return val.toFixed(1) + 'kPa';
        if (tab === 'flow') return val.toFixed(4);
        return val.toFixed(1) + '°C';
    }

    function _lsmUpdatePPLegend(seriesNames) {
        var legend = document.getElementById('lsm-chart-legend');
        if (!legend) return;
        var fs = window._lsmFontScale || 1.0;
        legend.style.fontSize = Math.round(10 * fs) + 'px';
        legend.innerHTML = '';

        if (seriesNames.length === 0) {
            var empty = document.createElement('div');
            empty.style.cssText = 'color:#666; line-height:1.4;';
            empty.textContent = t('lsm.pp_no_data');
            legend.appendChild(empty);
            return;
        }

        for (var i = 0; i < seriesNames.length; i++) {
            var name = seriesNames[i];
            var color = _lsmColors[i % _lsmColors.length];
            var hidden = !!window._lsmHiddenSeries[name];
            var item = document.createElement('div');
            item.style.cssText = 'cursor:pointer; display:flex; align-items:flex-start; gap:6px; ' +
                'padding:3px 4px; border:1px solid #2f3448; border-radius:3px; background:#151a28;' +
                (hidden ? ' opacity:0.3;' : '');
            item.innerHTML = '<span style="display:inline-block; width:10px; height:3px; margin-top:6px; flex-shrink:0; background:' + color + '; border-radius:999px;"></span>' +
                '<span style="min-width:0; overflow-wrap:anywhere; word-break:break-word; line-height:1.3; font-size:9px;">' + name + '</span>';
            item.setAttribute('data-series', name);
            item.addEventListener('click', function() {
                var s = this.getAttribute('data-series');
                window._lsmHiddenSeries[s] = !window._lsmHiddenSeries[s];
                _lsmDrawPPChart();
            });
            legend.appendChild(item);
        }
    }

    console.log('[liquid_solve_monitor.js] Liquid solver monitor loaded');
})();
