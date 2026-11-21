"""dc.cfd_bridge — GTC auto-load + WebRTC data-channel command dispatch.

Dispatch pattern copied from the Omniverse DSX blueprint `manager` extension:
subscribe directly to the carb event `omni.kit.livestream.receive_message`
(no omni.kit.livestream.messaging dependency), decode the
`send_message_from_event` envelope, route by command_name.
"""
import os
import json
import asyncio
import re

import omni.ext
import omni.usd
import omni.kit.app
import carb.events
import carb.eventdispatcher

from . import parsers

__all__ = ["CfdBridgeExtension"]


class CfdBridgeExtension(omni.ext.IExt):
    _RECEIVE_EVENT = "omni.kit.livestream.receive_message"
    # Acked OV-display commands; they ride the render path (requestId + ack).
    OV_DISPLAY_COMMANDS = frozenset({'setSlice', 'getSliceState', 'loadResultDataset', 'setVolumeDisplay',
                                     'loadLiquidResult', 'setLiquidFrame', 'clearLiquidColours'})
    # The livestream WebRTC plugin (omni.kit.livestream.webrtc 9.0.2) silently
    # drops an outbound custom message queued within about a frame of an
    # inbound one. Measured on 40901 with a relay probe (2026-09-30): echoes
    # pushed synchronously or one frame after a received command were lost 3/3,
    # echoes five frames later arrived 3/3, and every ack sent inside the
    # command callback vanished. Acks therefore leave through a queue that waits
    # for this many inbound-quiet frames, in order.
    # The browser's outbound-readiness handshake covers the session-start drop
    # window, so this only guards against back-to-back inbound traffic; each
    # progress ack of a chunked scene pays it, so keep it short. Env override
    # for measurement.
    ACK_QUIET_FRAMES = int(os.environ.get('DC_CFD_ACK_QUIET_FRAMES', '2'))
    ACK_MAX_WAIT_FRAMES = 120
    # Liquid result colours also light themselves this much (emissive = glow *
    # rgb): most Shinaide pipework runs under a translucent raised floor whose
    # lit top otherwise hides the shadowed, painted parts in RTX.
    LIQUID_GLOW = float(os.environ.get('DC_CFD_LIQUID_GLOW', '0.35'))

    def on_startup(self, _ext_id):
        print("[dc.cfd_bridge] startup")
        self._scene = None  # set in Task 1.3
        from .render_scene import SceneTransfer, validate_overlay
        from .result_layers_usd import validate_result_transfer
        import omni.kit.livestream.messaging as messaging
        self._render_transfer = SceneTransfer()
        self._overlay_transfer = SceneTransfer(validator=validate_overlay)
        self._result_transfer = SceneTransfer(validator=validate_result_transfer)
        # No WebRTC command may populate this. The backend currently has no
        # complete final-patch/run/field certificate for Kit to verify.
        self._trusted_result_context = None
        self._render_uid_paths = {}
        self._render_room_shell_paths = ()
        self._render_primitive_ids = {}
        self._render_project_hash = None
        self._scene_revision = 0
        # Request id of the last authored editor scene. getViewState reports
        # it so a browser whose scene ack was dropped by the livestream plugin
        # (NVST_R_BUSY) can confirm the load from Kit's own state.
        self._last_scene_request_id = None
        # Project whose scene was showing when the current result dataset was
        # loaded. A scene for another project clears the air display state
        # (volume, slice, streamlines, dataset) so one case's results never
        # overlay another's geometry.
        self._scene_project_uid = None
        self._display_project_uid = None
        # A new editor scene invalidates pending display work immediately.
        # Its authoring and dataset imports share a barrier: the VTK importer
        # writes the live stage while awaiting, so a completion check alone
        # cannot keep an old import away from new engineering geometry.
        self._display_generation = 0
        self._display_commit_lock = asyncio.Lock()
        self._last_ov_pick = None
        self._suppress_ov_pick = False
        self._inbound_seq = 0
        # Liquid result colouring: its OWN trusted context, set only from a
        # verified server grant whose file hash re-checks here. Never shared
        # with _trusted_result_context (air result layers stay fail-closed).
        self._liquid = None
        self._liquid_saved = {}
        self._ack_queue = []
        self._ack_flushing = False
        messaging.register_event_type_to_send('dcdtuni:renderAck')
        messaging.register_event_type_to_send('dcdtuni:ovEditEvent')
        self._stage_event_sub = omni.usd.get_context().get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event, name='dcdtuni OV source selection')

        # config from env (set by start_demo.sh)
        self._vtu = os.environ.get("DC_CFD_VTU", "")
        self._json = os.environ.get("DC_CFD_JSON", "")
        self._geo = os.environ.get("DC_CFD_GEO", "")
        self._ranges_path = os.environ.get("DC_CFD_RANGES", "")

        # WebRTC data-channel subscription (blueprint manager pattern)
        self._registered_alias = False
        event_type = carb.events.type_from_string(self._RECEIVE_EVENT)
        if omni.kit.app.register_event_alias(event_type, self._RECEIVE_EVENT):
            self._registered_alias = True
        self._webrtc_sub = carb.eventdispatcher.get_eventdispatcher().observe_event(
            event_name=self._RECEIVE_EVENT,
            on_event=self._on_webrtc_message,
        )

        # auto-load the GTC scene (Task 1.3 fills in load_scene)
        asyncio.ensure_future(self._startup_async())

    async def _startup_async(self):
        from .scene import CfdScene
        # The scene is NOT built at startup. The geometry list is empty until the
        # user clicks "Load CFD Geometry", picks a scene-definition JSON, and the
        # loadScene command builds + reveals it (handle_load_scene). This keeps the
        # default state clean (no geometry) and matches the dataset-load flow.
        self._scene = CfdScene(self._vtu, self._json, self._geo, self._ranges_path)
        from .handlers import install_render_room_shell_paths
        install_render_room_shell_paths(self._scene, omni.usd.get_context().get_stage(),
                                       self._render_room_shell_paths)
        # Render-only: strip the native editor chrome so the stream shows only
        # the 3D viewport. The web React UI is the sole control surface (user
        # requirement: "render-only viewport, NOT native OV UI").
        try:
            await self._hide_native_ui()
        except Exception as e:
            print(f"[dc.cfd_bridge] hide_native_ui ERROR: {e}")

    async def _hide_native_ui(self):
        """Strip the native editor chrome so only the 3D render streams, while
        keeping the RTX render intact.

        We deliberately do NOT use ``/app/window/hideUi``: in the headless
        ``--no-window`` streaming config it suppresses the RTX geometry render
        (only scene-overlay layers such as the grid survive). Instead we hide
        the viewport's menubar + axis-gizmo *layers* directly — the exact layers
        ``ViewportWindow.hide_ui`` toggles — plus the FPS/memory HUD, the docked
        side/bottom panels, the main menu bar and the status bar. None of these
        touch the render product. Each step is guarded so one failure cannot
        leave the UI half-stripped."""
        app = omni.kit.app.get_app()
        # Let the editor build + auto-dock its windows first.
        for _ in range(90):
            await app.next_update_async()

        # FPS/memory HUD off (does not affect the render product).
        try:
            import carb.settings
            s = carb.settings.get_settings()
            s.set("/persistent/app/viewport/Viewport/Viewport0/hud/visible", False)
            s.set("/app/viewport/forceHideFps", True)
            # Hide the legacy_gizmos editor overlay ICONS (the yellow "sun"
            # light gizmos, camera gizmos, audio) — they are editor aids, not
            # part of the data-center scene, and must not appear in the render.
            # The actual lights under /World/Env still illuminate the scene.
            s.set("/app/viewport/show/lights", False)
            # Disable the native procedural viewport grid: re-investigated per user
            # request, it is zoom-adaptive (cannot be pinned to 0.6 m) and renders only
            # its axis lines (no cells) in the --no-window stream. The custom 0.6 m
            # /World/FloorGrid (scene.py) is used instead, made non-selectable so it is
            # not a pickable USD object.
            s.set("/app/viewport/grid/enabled", False)
            s.set("/app/viewport/show/cameras", False)
            s.set("/app/viewport/show/audio", False)
        except Exception as e:
            print(f"[dc.cfd_bridge] hide_ui: hud settings failed: {e}")

        # Hide the viewport's own menubar layer (without the window-flag changes
        # that break the render). The Axis orientation gizmo is kept on purpose
        # — it is a useful render-view orientation aid, not editor chrome.
        try:
            from omni.kit.viewport.window import get_viewport_window_instances
            for vw in get_viewport_window_instances():
                for name, cat in (("Menubar", "menubar"),):
                    try:
                        layer = vw._find_viewport_layer(name, cat)
                        if layer is not None:
                            layer.visible = False
                    except Exception:
                        pass
                try:
                    vw.dock_tab_bar_enabled = False
                except Exception:
                    pass
        except Exception as e:
            print(f"[dc.cfd_bridge] hide_ui: viewport layers failed: {e}")

        # Hide every docked panel; the viewport then expands to fill the frame.
        try:
            import omni.ui as ui
            keep = {"Viewport", "DockSpace"}
            for w in ui.Workspace.get_windows():
                try:
                    if w.title not in keep:
                        w.visible = False
                except Exception:
                    pass
        except Exception as e:
            print(f"[dc.cfd_bridge] hide_ui: window sweep failed: {e}")

        # Hide the main menu bar + status bar.
        try:
            from omni.kit.mainwindow import get_main_window
            mw = get_main_window()
            mw.get_main_menu_bar().visible = False
            mw.get_status_bar_frame().visible = False
        except Exception as e:
            print(f"[dc.cfd_bridge] hide_ui: menubar/status hide failed: {e}")

        for _ in range(5):
            await app.next_update_async()
        print("[dc.cfd_bridge] native UI hidden (render-only viewport)")

    def on_shutdown(self):
        print("[dc.cfd_bridge] shutdown")
        import omni.kit.livestream.messaging as messaging
        messaging.unregister_event_type_to_send('dcdtuni:renderAck')
        messaging.unregister_event_type_to_send('dcdtuni:ovEditEvent')
        self._stage_event_sub = None
        if getattr(self, "_webrtc_sub", None):
            self._webrtc_sub.reset()
            self._webrtc_sub = None
        if getattr(self, "_registered_alias", False):
            event_type = carb.events.type_from_string(self._RECEIVE_EVENT)
            carb.events.unregister_event_alias(
                event_type, f"{self._RECEIVE_EVENT}:immediate", self._RECEIVE_EVENT
            )
            self._registered_alias = False

    def _on_webrtc_message(self, event):
        self._inbound_seq += 1
        if "message" not in event.payload:
            return
        try:
            env = json.loads(event.payload["message"])
        except (json.JSONDecodeError, TypeError):
            return
        if env.get("event_type") != "send_message_from_event":
            return
        payload = env.get("payload")
        if payload:
            self._route_command(payload)

    def _route_command(self, payload):
        command_name = payload.get("command_name", "")
        message = payload.get("message", "")
        if command_name in {'beginRenderScene', 'renderSceneChunk', 'renderSceneProgress', 'loadRenderScene',
                            'beginRenderOverlay', 'renderOverlayChunk', 'renderOverlayProgress', 'loadRenderOverlay',
                            'beginRenderResultLayers', 'renderResultLayersChunk', 'renderResultLayersProgress',
                            'loadRenderResultLayers', 'clearRenderResultLayers',
                            'updateViewState', 'getViewState'} | self.OV_DISPLAY_COMMANDS:
            # Never print embedded mesh/texture data into the long-lived Kit log.
            self._route_render_command(command_name, message)
            return
        print(f"[dc.cfd_bridge] command={command_name} message={message}")
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if not stage or not self._scene:
            print("[dc.cfd_bridge] no stage/scene yet")
            return
        from . import handlers, parsers
        s = self._scene
        if command_name in {'loadScene', 'resetScene'}:
            from pxr import UsdGeom
            editor_scene = stage.GetPrimAtPath('/World/RenderScene')
            if editor_scene:
                UsdGeom.Imageable(editor_scene).MakeInvisible()
        if command_name == "loadScene":
            handlers.handle_load_scene(s, stage, parsers.parse_load_scene(message))
        elif command_name == "setCamera":
            handlers.handle_set_camera(stage, message)
        elif command_name == "setVolume":
            field, visible = parsers.parse_set_volume(message)
            handlers.handle_set_volume(s, stage, field, visible)
        elif command_name == "setVolumeRange":
            vmin, vmax = parsers.parse_set_volume_range(message)
            handlers.handle_set_volume_range(s, stage, vmin, vmax)
        elif command_name == "setRoiVisible":
            handlers.handle_set_roi_visible(s, stage, parsers.parse_set_roi_visible(message))
        elif command_name == "setRoiBox":
            mn, mx = parsers.parse_set_roi_box(message)
            handlers.handle_set_roi_box(s, stage, mn, mx)
        elif command_name == "setRoiClip":
            handlers.handle_set_roi_clip(s, stage, parsers.parse_set_roi_clip(message))
        elif command_name == "setObjectVisible":
            name, visible = parsers.parse_set_object_visible(message)
            handlers.handle_set_object_visible(s, stage, name, visible)
        elif command_name == "setObjectOpacity":
            name, opacity = parsers.parse_set_object_opacity(message)
            handlers.handle_set_object_opacity(s, stage, name, opacity)
        elif command_name == "selectObject":
            handlers.handle_select_object(s, stage, parsers.parse_select_object(message))
        elif command_name == "setGrid":
            handlers.handle_set_grid(s, stage, **parsers.parse_set_grid(message))
        elif command_name == "focusObject":
            handlers.handle_focus_object(s, stage, parsers.parse_select_object(message))
        elif command_name == "extractRoi":
            handlers.handle_extract_roi(s, stage, parsers.parse_extract_roi(message))
        elif command_name == "loadDataset":
            handlers.handle_load_dataset(s, stage, parsers.parse_load_dataset(message))
        elif command_name == "setStreamlines":
            asyncio.ensure_future(self._legacy_display(stage, handlers.handle_set_streamlines,
                parsers.parse_set_streamlines(message), self._display_generation))
        elif command_name == "setSeedSphere":
            asyncio.ensure_future(self._legacy_display(stage, handlers.handle_set_seed_sphere,
                parsers.parse_set_seed_sphere(message), self._display_generation))
        elif command_name == "syncFromViewport":
            asyncio.ensure_future(self._legacy_display(stage, handlers.handle_sync_from_viewport,
                {}, self._display_generation))
        elif command_name == "resetScene":
            handlers.handle_reset_scene(s, stage)
        else:
            print(f"[dc.cfd_bridge] unknown command: {command_name}")

    def _ack_after(self, request_id, command, coro):
        """Run an async render command and ack with its result dict, or with
        ok=False and the reason; the ack channel never goes silent."""
        async def _run():
            try:
                result = await coro
                self._render_ack(request_id, ok=True, **result)
            except Exception as exc:  # noqa: BLE001 — surfaced to the browser
                print(f'[dc.cfd_bridge] render command {command} failed: {exc}')
                self._render_ack(request_id, ok=False, error=str(exc))
        asyncio.ensure_future(_run())

    async def _load_liquid(self, stage, granted):
        """Read the granted result.json, re-check its hash, bind it to the
        loaded editor scene, and make it the liquid colouring context."""
        import hashlib
        from . import liquid_colour, render_scene as _rs

        def _read():
            with open(granted['path'], 'rb') as f:
                raw = f.read()
            return hashlib.sha256(raw).hexdigest(), raw

        _digest, raw = await asyncio.get_event_loop().run_in_executor(None, _read)
        scene = stage.GetPrimAtPath('/World/RenderScene')
        scene_uid = scene.GetCustomDataByKey('dcdtuni:projectUid') if scene else None
        result, matches = await asyncio.get_event_loop().run_in_executor(
            None, lambda: liquid_colour.bind_result(granted, raw, scene_uid, self._render_project_hash))
        _rs.restore_colours(stage, self._liquid_saved)
        self._liquid = {'result': result, 'grant': granted, 'sample': None, 'domains': None}
        kinds = {}
        for t in result.targets:
            kinds[t[0]] = kinds.get(t[0], 0) + 1
        print(f"[dc.cfd_bridge] liquid result loaded run={granted['runId']} samples={result.samples} targets={kinds}")
        return {'liquid': {
            'runId': granted['runId'], 'solver': granted['solver'], 'status': granted['status'],
            'samples': result.samples, 'times': [result.times[0], result.times[-1]],
            'targets': kinds, 'projectUid': granted['projectUid'],
            'projectContentMatches': matches,
        }}

    def _paint_liquid(self, stage, sample, domains):
        """Colour every targeted liquid part for one sample on its own
        material; report what was painted and what had no primitive."""
        from . import render_scene as _rs
        frame = self._liquid['result'].frame(sample, domains)
        index = _rs.index_render_prims(stage)
        by_model_equip = {}
        for (model, equip, part), paths in index.items():
            by_model_equip.setdefault((model, equip), []).extend((part, p) for p in paths)
        colours, unmatched = {}, {}
        for t in frame['targets']:
            if t['kind'] in ('manifold',):
                paths = index.get((t['modelUid'], '', t['liquidPart']), [])
            elif t['kind'] == 'pipe':
                paths = [p for part, p in by_model_equip.get((t['modelUid'], ''), []) if not part]
            elif t['kind'] == 'cdu':
                paths = [p for (m, _e), items in by_model_equip.items() if m == t['modelUid'] for _part, p in items]
            else:  # server / tee / valve: the part's equipment primitives
                paths = [p for _part, p in by_model_equip.get((t['modelUid'], t['equipmentUid']), [])]
            if not paths:
                unmatched[t['kind']] = unmatched.get(t['kind'], 0) + 1
            rgb = tuple(c / 255.0 for c in t['rgb'])
            for p in paths:
                colours[p] = rgb
        painted = _rs.override_colours(stage, colours, self._liquid_saved, glow=self.LIQUID_GLOW)
        self._liquid.update(sample=frame['sample'], domains=domains)
        counts = {}
        for t in frame['targets']:
            counts[t['kind']] = counts.get(t['kind'], 0) + 1
        return {'sample': frame['sample'], 'timeSeconds': frame['timeSeconds'], 'groups': frame['groups'],
                'targets': counts, 'painted': painted, 'unmatched': unmatched}

    async def _display_mutation(self, generation, operation):
        """Serialize actual display writers with dataset/scene installation.

        operation is lazy: a queued old command cannot even change the new
        scene's parameters. No new ACK is introduced for legacy fire commands.
        """
        async with self._display_commit_lock:
            if generation != self._display_generation:
                raise RuntimeError('display command refused: scene changed while queued')
            return await operation()

    async def _legacy_display(self, stage, handler, args, generation):
        async def operation():
            pending = []
            try:
                # Preserve handler arithmetic; capture its coroutine instead
                # of launching a second background task outside the barrier.
                handler(self._scene, stage, **args, _schedule=pending.append)
                for coroutine in pending:
                    await coroutine
            finally:
                for coroutine in pending:
                    coroutine.close()
        try:
            await self._display_mutation(generation, operation)
        except Exception as exc:  # legacy fire has no ACK; failure stays visible
            print(f'[dc.cfd_bridge] display command {handler.__name__} failed: {exc}')

    async def _set_slice(self, stage, parsed):
        from . import handlers
        handlers.handle_set_slice(self._scene, stage, **parsed)
        return await self._slice_rebuild(stage)

    async def _volume_display(self, stage, field, domain):
        return {'volume': await self._scene.show_volume_display(stage, field, domain)}

    async def _slice_rebuild(self, stage):
        summary = await self._scene.update_slice(stage)
        return {'slice': self._slice_state(), 'sliceResult': summary}

    async def _slice_readback(self, stage):
        adopted = self._scene.adopt_dragged_slice(stage) is not None
        summary = await self._scene.update_slice(stage) if adopted else self._scene._slice_summary
        return {'slice': self._slice_state(), 'sliceResult': summary, 'adopted': adopted}

    async def _clear_display(self, stage):
        """Drop the previous project's result display: streamlines, slice,
        volume and the dataset references later commands would act on."""
        s = self._scene
        try:
            await s.remove_streamlines(stage)
            await s.remove_slice(stage)
            s.slice_visible = False
            await s.show_volume_display(stage, 'off')
        except Exception as exc:  # noqa: BLE001
            print(f'[dc.cfd_bridge] display clear incomplete: {exc}')
        # A failed result teardown must not carry our temporary enclosure mask forward.
        from .handlers import _set_geometry_hidden
        _set_geometry_hidden(s, stage, False)
        if stage.GetPrimAtPath('/World/internal').IsValid():
            stage.RemovePrim('/World/internal')
        s.dataset_path = None
        s.t_field = s.u_field = s.umag_field = None
        s.field_stats = None
        # Slice extraction also reads these source references directly.
        s.vtu_path = s.source_vtu_path = None
        s._slice_grid = s._slice_cache_key = s._slice_summary = None
        self._display_project_uid = None
        print('[dc.cfd_bridge] result display cleared')

    async def _load_granted_dataset(self, stage, granted, generation):
        async with self._display_commit_lock:
            def require_current_scene():
                if generation != self._display_generation:
                    raise RuntimeError('dataset load refused: scene changed while loading')

            # Reject a queued, superseded load without clearing a newer result.
            require_current_scene()
            try:
                ok = await self._scene.load_dataset(stage, granted['path'])
                require_current_scene()
                if not ok:
                    raise RuntimeError('dataset load failed (see the Kit log for the reader error)')
                self._display_project_uid = self._scene_project_uid
                s = self._scene
                # Bounds let the panel place the slice disc and seed sphere inside the
                # data. The same read primes the slice's grid cache.
                from . import slice_render
                src = getattr(s, 'source_vtu_path', None) or s.vtu_path
                grid = await asyncio.get_event_loop().run_in_executor(None, slice_render.read_grid, src)
                require_current_scene()
                s._slice_grid = (src, grid)
                b = grid.GetBounds()
                bounds = [float(v) for v in b] if b[0] <= b[1] else None
                if s.slice_visible:
                    await s.update_slice(stage)
                    require_current_scene()
                return {'dataset': {
                    'bounds': bounds,
                    'run': granted['run'], 'runId': granted['runId'], 'time': granted['time'],
                    'fields': s.field_stats or {},
                    'ranges': {k: [float(v[0]), float(v[1])] for k, v in (s.ranges or {}).items()},
                    'hasT': bool(s.t_field), 'hasU': bool(s.u_field),
                }}
            finally:
                if generation != self._display_generation:
                    # Let the importer settle, then remove its writes while the
                    # new scene is still waiting on the same commit barrier.
                    await self._clear_display(stage)

    async def _author_render_scene(self, stage, packet, project_hash, request_id, generation):
        from . import render_scene
        async with self._display_commit_lock:
            if generation != self._display_generation:
                raise RuntimeError('scene load refused: a newer scene request superseded it')
            if (self._display_project_uid is not None and self._scene is not None
                    and packet['project']['uid'] != self._display_project_uid):
                await self._clear_display(stage)
            # A newer request may arrive while the previous display is retiring.
            if generation != self._display_generation:
                raise RuntimeError('scene load refused: a newer scene request superseded it')
            print(f'[dc.cfd_bridge] scene load begin primitives={len(packet["primitives"])}')
            from .owned_paths import render_texture_cache_path
            cache = render_texture_cache_path()
            result = render_scene.author_scene(stage, packet, cache, validated=True)
            print(f'[dc.cfd_bridge] scene load authored primitives={result["primitives"]}')
            from .handlers import install_render_room_shell_paths
            self._render_room_shell_paths = tuple(result.get('roomShellPaths', ()))
            if self._scene is not None:
                install_render_room_shell_paths(self._scene, stage, self._render_room_shell_paths)
            self._render_uid_paths = result['uidPaths']
            self._render_primitive_ids = {p['id']: p['modelUid'] for p in packet['primitives']}
            self._render_project_hash = project_hash
            self._last_scene_request_id = request_id
            self._scene_project_uid = packet['project']['uid']
            # The scene's materials were re-authored: saved originals are gone.
            self._liquid_saved = {}
            if self._liquid is not None:
                if packet['project']['uid'] != self._liquid['grant']['projectUid']:
                    self._liquid = None  # a different project: the result no longer applies
                elif self._liquid['sample'] is not None:
                    self._paint_liquid(stage, self._liquid['sample'], self._liquid['domains'])
            self._scene_revision += 1
            self._last_ov_pick = None
            from pxr import UsdGeom
            for path in ['/World/Geometry', '/World/FloorGrid']:
                old = stage.GetPrimAtPath(path)
                if old:
                    UsdGeom.Imageable(old).MakeInvisible()
            return self._apply_render_view(stage, packet['view'])

    def _apply_render_view(self, stage, view):
        from . import render_scene
        if view.get('camera'):
            from omni.kit.viewport.utility import get_active_viewport
            viewport = get_active_viewport()
            if viewport:
                viewport.camera_path = render_scene.CAMERA
        ctx = omni.usd.get_context()
        paths = [path for uid in view['selectedUids'] for path in self._render_uid_paths.get(uid, [])]
        self._suppress_ov_pick = True
        try:
            ctx.get_selection().set_selected_prim_paths(paths, True)
        finally:
            self._suppress_ov_pick = False
        return {'view': view, 'primitives': sum(len(x) for x in self._render_uid_paths.values()),
                'sceneRevision': self._scene_revision, 'projectHash': self._render_project_hash}

    def _slice_state(self):
        s = self._scene
        return {'visible': s.slice_visible, 'origin': list(s.slice_origin),
                'normal': list(s.slice_normal), 'radius': s.slice_radius,
                'field': s.slice_field, 'domain': s.slice_domain}

    def _render_ack(self, request_id, **result):
        self._ack_queue.append({'requestId': request_id, **result})
        if not self._ack_flushing:
            self._ack_flushing = True
            asyncio.ensure_future(self._flush_acks())

    async def _flush_acks(self):
        """Push queued acks once the inbound side has been quiet for
        ACK_QUIET_FRAMES frames (capped, so a steady inbound stream such as a
        camera drag cannot starve them). See ACK_QUIET_FRAMES."""
        app = omni.kit.app.get_app()
        event = carb.events.type_from_string('dcdtuni:renderAck')
        try:
            while self._ack_queue:
                quiet = waited = 0
                while quiet < self.ACK_QUIET_FRAMES and waited < self.ACK_MAX_WAIT_FRAMES:
                    seen = self._inbound_seq
                    await app.next_update_async()
                    waited += 1
                    quiet = quiet + 1 if self._inbound_seq == seen else 0
                batch, self._ack_queue = self._ack_queue, []
                for payload in batch:
                    app.get_message_bus_event_stream().push(event, payload=payload)
        finally:
            self._ack_flushing = False

    def _on_stage_event(self, event):
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        self._emit_source_pick()

    def _emit_source_pick(self):
        """Publish only a selected, acknowledged source-geometry primitive."""
        if self._suppress_ov_pick or not self._render_project_hash:
            return
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            return
        for path in ctx.get_selection().get_selected_prim_paths():
            if not path.startswith('/World/RenderScene/Geometry/'):
                continue
            prim = stage.GetPrimAtPath(path)
            if not prim:
                continue
            prim_id = prim.GetCustomDataByKey('dcdtuni:primitiveId')
            model_uid = prim.GetCustomDataByKey('dcdtuni:modelUid')
            if self._render_primitive_ids.get(prim_id) != model_uid or not model_uid:
                continue
            identity = (self._scene_revision, prim_id)
            if identity == self._last_ov_pick:
                return
            self._last_ov_pick = identity
            signal = carb.events.type_from_string('dcdtuni:ovEditEvent')
            omni.kit.app.get_app().get_message_bus_event_stream().push(signal, payload={
                'schema': 'dcdtuni.ov-edit/v1',
                'sceneRevision': self._scene_revision,
                'projectHash': self._render_project_hash,
                'primId': prim_id, 'modelUid': model_uid,
                'coordinateSystem': {'upAxis': 'Y', 'handedness': 'right', 'metersPerUnit': 1},
                'action': {'type': 'pick'},
            })
            return
        self._last_ov_pick = None

    def _route_render_command(self, command, message):
        from . import render_scene
        request_id = ''
        try:
            args = json.loads(message)
            if not isinstance(args, dict) or not isinstance(args.get('requestId'), str):
                raise ValueError('Render command requires requestId')
            request_id = args['requestId']
            allowed = {
                'beginRenderScene': {'requestId', 'chunks'},
                'renderSceneChunk': {'requestId', 'index', 'data'},
                'renderSceneProgress': {'requestId', 'sceneRequestId', 'received'},
                'loadRenderScene': {'requestId'},
                'beginRenderOverlay': {'requestId', 'chunks'},
                'renderOverlayChunk': {'requestId', 'index', 'data'},
                'renderOverlayProgress': {'requestId', 'overlayRequestId', 'received'},
                'loadRenderOverlay': {'requestId'},
                'beginRenderResultLayers': {'requestId', 'chunks'},
                'renderResultLayersChunk': {'requestId', 'index', 'data'},
                'renderResultLayersProgress': {'requestId', 'resultRequestId', 'received'},
                'loadRenderResultLayers': {'requestId'},
                'clearRenderResultLayers': {'requestId'},
                'updateViewState': {'requestId', 'view'},
                'getViewState': {'requestId'},
                'setSlice': {'requestId', 'visible', 'origin', 'normal', 'radius',
                             'field', 'domain'},
                'getSliceState': {'requestId'},
                'loadResultDataset': {'requestId', 'grant'},
                'setVolumeDisplay': {'requestId', 'field', 'domain'},
                'loadLiquidResult': {'requestId', 'grant'},
                'setLiquidFrame': {'requestId', 'sample', 'domains'},
                'clearLiquidColours': {'requestId'},
            }
            if set(args) != allowed[command] and not (
                    command == 'loadRenderScene' and set(args) == {'requestId', 'projectHash'}):
                raise ValueError('Unexpected or missing render command fields')
            if command in {'beginRenderResultLayers', 'renderResultLayersChunk',
                           'renderResultLayersProgress', 'loadRenderResultLayers'} and (
                    self._trusted_result_context is None):
                raise ValueError('Trusted renderer provenance is unavailable')
            if command == 'beginRenderScene':
                self._render_transfer.begin(request_id, args['chunks'])
                print(f'[dc.cfd_bridge] scene begin chunks={args["chunks"]}')
                return
            if command == 'renderSceneChunk':
                self._render_transfer.chunk(request_id, args['index'], args['data'])
                return
            if command == 'renderSceneProgress':
                expected = args['received']
                if type(expected) is not int or expected < 0:
                    raise ValueError('Invalid scene progress count')
                actual = self._render_transfer.progress(args['sceneRequestId'])
                if actual != expected:
                    raise ValueError(f'Scene progress mismatch: expected {expected}, actual {actual}')
                self._render_ack(request_id, ok=True, received=actual)
                print(f'[dc.cfd_bridge] scene progress received={actual}')
                return
            if command == 'beginRenderOverlay':
                self._overlay_transfer.begin(request_id, args['chunks'])
                print(f'[dc.cfd_bridge] overlay begin chunks={args["chunks"]}')
                return
            if command == 'renderOverlayChunk':
                self._overlay_transfer.chunk(request_id, args['index'], args['data'])
                return
            if command == 'renderOverlayProgress':
                expected = args['received']
                if type(expected) is not int or expected < 0:
                    raise ValueError('Invalid overlay progress count')
                actual = self._overlay_transfer.progress(args['overlayRequestId'])
                if actual != expected:
                    raise ValueError(f'Overlay progress mismatch: expected {expected}, actual {actual}')
                self._render_ack(request_id, ok=True, received=actual)
                print(f'[dc.cfd_bridge] overlay progress received={actual}')
                return
            if command == 'beginRenderResultLayers':
                self._result_transfer.begin(request_id, args['chunks'])
                return
            if command == 'renderResultLayersChunk':
                self._result_transfer.chunk(request_id, args['index'], args['data'])
                return
            if command == 'renderResultLayersProgress':
                expected = args['received']
                if type(expected) is not int or expected < 0:
                    raise ValueError('Invalid result progress count')
                actual = self._result_transfer.progress(args['resultRequestId'])
                if actual != expected:
                    raise ValueError(f'Result progress mismatch: expected {expected}, actual {actual}')
                self._render_ack(request_id, ok=True, received=actual)
                return
            ctx = omni.usd.get_context()
            stage = ctx.get_stage()
            if stage is None:
                raise ValueError('Kit stage is not ready')
            if command == 'clearRenderResultLayers':
                if stage.GetPrimAtPath('/World/RenderResultLayers'):
                    if not stage.RemovePrim('/World/RenderResultLayers'):
                        raise RuntimeError('Could not clear render result layers')
                self._render_ack(request_id, ok=True, layers=0)
                return
            if command == 'loadRenderResultLayers':
                from .result_layers_usd import (author_result_layers,
                    require_acknowledged_scene_hash, require_trusted_result_context)
                transfer = self._result_transfer.finish(request_id)
                scene = stage.GetPrimAtPath('/World/RenderScene')
                if not scene:
                    raise ValueError('No editor render scene loaded')
                source = transfer['expectedSource']
                if scene.GetCustomDataByKey('dcdtuni:projectUid') != source['projectUid']:
                    raise ValueError('Result source project does not match loaded scene')
                require_acknowledged_scene_hash(source, self._render_project_hash)
                require_trusted_result_context(transfer, self._trusted_result_context)
                result = author_result_layers(stage, transfer['packet'],
                    expected_source=source,
                    expected_time_seconds=transfer['expectedTimeSeconds'])
                self._render_ack(request_id, ok=True, revision=result['revision'],
                                 layers=len(result['layerPaths']))
                return
            if command == 'loadRenderOverlay':
                packet = self._overlay_transfer.finish(request_id)
                result = render_scene.author_overlay(stage, packet)
                self._render_ack(request_id, ok=True, primitives=result['primitives'])
                print(f'[dc.cfd_bridge] overlay load authored primitives={result["primitives"]}')
                return
            if command == 'loadRenderScene':
                packet = self._render_transfer.finish(request_id)
                project_hash = args.get('projectHash')
                if project_hash is not None and (not isinstance(project_hash, str) or
                        not re.fullmatch(r'[0-9a-f]{64}', project_hash)):
                    raise ValueError('Invalid project content hash')
                self._display_generation += 1
                self._ack_after(request_id, command, self._author_render_scene(
                    stage, packet, project_hash, request_id, self._display_generation))
                return
            elif command == 'setSlice':
                from . import handlers, parsers
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                parsed = parsers.parse_set_slice(message)
                # Adopt parameters and author the cut inside the commit barrier.
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._set_slice(stage, parsed)))
                return
            elif command == 'getSliceState':
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                # "Read back the viewport drag": adopt a dragged preview disc,
                # re-cut there, and report the state Kit now holds.
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._slice_readback(stage)))
                return
            elif command == 'loadLiquidResult':
                from . import display_grant
                import time as _time
                granted = display_grant.verify(
                    args['grant'], os.environ.get('DCDTUNI_OV_GRANT_KEY', ''), now=_time.time(),
                    roots=display_grant.roots_from_env(os.environ.get('DC_CFD_DATASET_ROOTS')),
                    kind='liquidResult')
                self._ack_after(request_id, command, self._load_liquid(stage, granted))
                return
            elif command == 'setLiquidFrame':
                if self._liquid is None:
                    raise ValueError('No verified liquid result is loaded (loadLiquidResult first)')
                sample = args['sample']
                if type(sample) is not int:
                    raise ValueError('sample must be an integer')
                domains = args['domains']
                if domains is not None and (not isinstance(domains, dict) or set(domains) - {'network', 'chips'}):
                    raise ValueError('domains must be null or {network?, chips?}')
                self._render_ack(request_id, ok=True, liquidFrame=self._paint_liquid(stage, sample, domains))
                return
            elif command == 'clearLiquidColours':
                from . import render_scene as _rs
                cleared = len(self._liquid_saved)
                _rs.restore_colours(stage, self._liquid_saved)
                self._liquid = None
                self._render_ack(request_id, ok=True, cleared=cleared)
                return
            elif command == 'setVolumeDisplay':
                from . import parsers
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                parsed = parsers.parse_set_volume_display(message)
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._volume_display(stage, **parsed)))
                return
            elif command == 'loadResultDataset':
                from . import display_grant
                import time as _time
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                granted = display_grant.verify(
                    args['grant'], os.environ.get('DCDTUNI_OV_GRANT_KEY', ''), now=_time.time(),
                    roots=display_grant.roots_from_env(os.environ.get('DC_CFD_DATASET_ROOTS')))
                self._ack_after(request_id, command, self._load_granted_dataset(
                    stage, granted, self._display_generation))
                return
            elif command == 'updateViewState':
                view = render_scene.apply_view(stage, args['view'])
            else:
                view = render_scene.read_view(stage, ctx.get_selection().get_selected_prim_paths())
                # Idempotent state query: also the scene identity a scene ack
                # carries, so a lost scene ack is recoverable (ovProtocol).
                self._render_ack(request_id, ok=True, view=view,
                                 primitives=sum(len(x) for x in self._render_uid_paths.values()),
                                 sceneRevision=self._scene_revision, projectHash=self._render_project_hash,
                                 sceneRequestId=self._last_scene_request_id)
                return
            self._render_ack(request_id, ok=True, **self._apply_render_view(stage, view))
        except Exception as exc:
            print(f'[dc.cfd_bridge] render command {command} rejected: {exc}')
            self._render_ack(request_id, ok=False, error=str(exc))


# ── Loading mechanism ───────────────────────────────────────────────────────
# Loaded as a normal Kit extension: start_demo.sh passes
# `--ext-folder .../kit/extensions --enable dc.cfd_bridge`, so Kit's dependency
# solver resolves the [dependencies] in config/extension.toml, then drives the
# standard IExt lifecycle (Kit instantiates CfdBridgeExtension and calls
# on_startup / on_shutdown). No manual bootstrap is needed.
