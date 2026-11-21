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
    OV_DISPLAY_COMMANDS = frozenset({'setSlice', 'getSliceState', 'loadResultDataset', 'setVolumeDisplay', 'setSurfaceDisplay',
                                     'loadLiquidResult', 'setLiquidFrame', 'clearLiquidColours',
                                     'setCommonPostprocessPreviews', 'retireCommonPostprocessPreviews'})
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
        from .display_diagnostics import ReadbackController
        import carb.settings
        self._bounded_display_readback = ReadbackController(
            carb.settings.get_settings().get('/exts/dc.cfd_bridge/boundedDisplayReadback/enabled'))
        from .render_scene import SceneTransfer, validate_overlay
        from .result_layers_usd import validate_result_transfer
        import omni.kit.livestream.messaging as messaging
        self._render_transfer = SceneTransfer()
        self._overlay_transfer = SceneTransfer(validator=validate_overlay)
        self._result_transfer = SceneTransfer(validator=validate_result_transfer)
        # No WebRTC command may populate this. The backend currently has no
        # complete final-patch/run/field certificate for Kit to verify.
        self._trusted_result_context = None
        from .verified_result_grant import VerifiedResultController
        self._verified_result_controller = VerifiedResultController()
        from .common_postprocess_previews import PreviewController
        self._postprocess_preview_controller = PreviewController(
            lambda: (self._result_scene_identity(omni.usd.get_context().get_stage()), self._display_generation),
            self._emit_postprocess_preview)
        self._render_uid_paths = {}
        self._render_room_shell_paths = ()
        self._render_primitive_ids = {}
        self._render_project_hash = None
        # The browser's key of the scene authored last (SHA-256 of its JSON
        # without the view); reuseRenderScene shows that scene again without a
        # transfer. None when the last load came without one.
        self._render_scene_key = None
        self._scene_revision = 0
        self._grid_raster = None
        from .output_layout import OwnedOutputLayout
        self._owned_output_layout = OwnedOutputLayout(
            carb.settings.get_settings().get('/exts/dc.cfd_bridge/ownedOutputLayout/enabled'))
        self._grid_frame_revision = 0
        self._grid_viewport_api = None
        self._grid_subscriptions = []
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
        from .source_selection_trace import SourceSelectionTrace, SETTING
        self._source_selection_trace = SourceSelectionTrace(carb.settings.get_settings().get(SETTING))
        self._inbound_seq = 0
        from .source_pick import SourcePickController
        self._source_pick_controller = SourcePickController()
        # Liquid result colouring: its OWN trusted context, set only from a
        # verified server grant whose file hash re-checks here. Never shared
        # with _trusted_result_context (air result layers stay fail-closed).
        self._liquid = None
        self._liquid_saved = {}
        # Flow arrows of the liquid result (liquid_arrows): the motion of the
        # arrows on the stage, and the update subscription that moves them.
        self._liquid_motion = None
        self._liquid_arrow_sub = None
        self._liquid_arrow_tick = 0.0
        self._ack_queue = []
        self._ack_flushing = False
        messaging.register_event_type_to_send('dcdtuni:renderAck')
        messaging.register_event_type_to_send('dcdtuni:ovEditEvent')
        messaging.register_event_type_to_send('dcdtuni:postprocessPreviewEvent')
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
        # Editor scenes the server hands off while a page's stream connects.
        self._scene_handoff = None
        self._closing = False
        asyncio.ensure_future(self._watch_scene_handoff())

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
            # OV displays the common React selection; modelling remains in Three.
            # Keep native selection/outline and camera/seed/slice interactions,
            # while the public select operation disables transform manipulators.
            # Both reviewed RTX modes must honour fractional PreviewSurface
            # opacity. Do not force a renderer mode or rewrite scene materials.
            for path, value in (
                ("/app/transform/operation", "select"),
                # Common React opacity means alpha blending, rather than glass
                # refraction. Preserve every authored material opacity value.
                ("/rtx/material/translucencyAsOpacity", True),
                ("/rtx/raytracing/fractionalCutoutOpacity", True),
                ("/rtx/pathtracing/fractionalCutoutOpacity", True),
            ):
                try:
                    s.set(path, value)
                except Exception:
                    print(f"[dc.cfd_bridge] display setting unavailable: {path}")
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
        # that break the render). React owns the shared axis and orientation cube.
        try:
            from omni.kit.viewport.window import get_viewport_window_instances
            for vw in get_viewport_window_instances():
                try:
                    viewport_id = vw.viewport_api.id
                    if (isinstance(viewport_id, str) and viewport_id
                            and len(viewport_id.encode("utf-8")) <= 128):
                        carb.settings.get_settings().set(
                            f"/persistent/app/viewport/{viewport_id}/guide/axis/visible", False)
                    else:
                        print("[dc.cfd_bridge] native axis setting unavailable")
                except Exception:
                    print("[dc.cfd_bridge] native axis setting unavailable")
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
        self._closing = True
        self._postprocess_preview_controller.close()
        self._source_pick_controller.close()
        layout = getattr(self, '_owned_output_layout', None)
        if layout is not None:
            layout.close()
        if self._grid_raster is not None:
            self._grid_raster.close()
        for subscription in self._grid_subscriptions:
            subscription.destroy()
        self._grid_subscriptions = []
        self._grid_viewport_api = None
        self._liquid_arrow_sub = None
        self._liquid_motion = None
        print("[dc.cfd_bridge] shutdown")
        import omni.kit.livestream.messaging as messaging
        messaging.unregister_event_type_to_send('dcdtuni:renderAck')
        messaging.unregister_event_type_to_send('dcdtuni:ovEditEvent')
        messaging.unregister_event_type_to_send('dcdtuni:postprocessPreviewEvent')
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
                            'loadRenderResultLayers', 'loadVerifiedRenderResultLayers', 'clearRenderResultLayers',
                            'updateViewState', 'getViewState', 'querySourcePick', 'reuseRenderScene'} | self.OV_DISPLAY_COMMANDS:
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

    def _result_scene_identity(self, stage):
        if omni.usd.get_context().get_stage() != stage:
            raise ValueError('active USD stage changed')
        prim = stage.GetPrimAtPath('/World/RenderScene')
        return {'projectUid': prim.GetCustomDataByKey('dcdtuni:projectUid') if prim else None,
                'projectContentSha256': self._render_project_hash, 'revision': self._scene_revision}

    async def _load_verified_result(self, stage, ticket):
        from .verified_result_grant import read_granted_packet
        from .result_layers_usd import author_result_layers,validate_result_packet
        granted = ticket.granted()
        packet = await asyncio.get_event_loop().run_in_executor(None, read_granted_packet, granted)
        validate_result_packet(packet)
        controller = self._verified_result_controller
        controller.require_loaded(ticket, packet, self._result_scene_identity(stage), self._display_generation)
        from .common_volume_reader import read_granted_volume
        grid=await asyncio.get_event_loop().run_in_executor(None,read_granted_volume,granted,packet)
        controller.require_loaded(ticket,packet,self._result_scene_identity(stage),self._display_generation)
        volume_carrier=None
        if grid is not None and packet['volume']['visible']:
            from .common_volume_nvdb import prepare_nvdb
            from pathlib import Path
            volume_carrier=await asyncio.get_event_loop().run_in_executor(None,prepare_nvdb,grid,Path(granted['path']).parent)
            controller.require_loaded(ticket,packet,self._result_scene_identity(stage),self._display_generation)
        async with self._display_commit_lock:
            controller.require_loaded(ticket, packet, self._result_scene_identity(stage), self._display_generation)
            result = author_result_layers(stage, packet, expected_source=granted['source'],
                expected_time_seconds=granted['timeSeconds'], volume_carrier=volume_carrier, commit_guard=lambda: controller.commit(
                    ticket, packet, lambda: self._result_scene_identity(stage), lambda: self._display_generation))
            self._postprocess_preview_controller.bind_committed(granted, self._display_generation)
            return {'resultLayers': result, 'revision': result['revision'], 'packetSha256': granted['packetSha256'],
                    'source': granted['source'], 'timeSeconds': granted['timeSeconds'],
                    'sceneRevision': granted['sceneRevision'], 'displayGeneration': granted['displayGeneration']}

    async def _clear_verified_result(self, stage, serial, generation):
        from .result_layers_usd import clear_result_layers
        async with self._display_commit_lock:
            with self._verified_result_controller.clear_commit(serial, generation, lambda: self._display_generation):
                result = clear_result_layers(stage)
                return {'cleared': result}

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

    def _paint_liquid(self, stage, sample, domains, display=None):
        """Colour every targeted liquid part for one sample on its own
        material; report what was painted and what had no primitive.
        ``display`` (liquid_colour.parse_display) picks the network quantity."""
        from . import liquid_colour, render_scene as _rs
        quantity, colormap = liquid_colour.parse_display(display)
        frame = self._liquid['result'].frame(sample, domains, quantity=quantity, colormap=colormap)
        index = _rs.index_render_prims(stage)
        by_model_equip = {}
        for (model, equip, part), paths in index.items():
            by_model_equip.setdefault((model, equip), []).extend((part, p) for p in paths)
        colours, unmatched = {}, {}
        for t in frame['targets']:
            if t['kind'] in ('manifold',):
                paths = index.get((t['modelUid'], '', t['liquidPart']), [])
            elif t['kind'] in ('pipe', 'junction'):
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
        self._liquid.update(sample=frame['sample'], domains=domains, display=display)
        from . import liquid_arrows
        arrows = self._liquid_arrows(stage, frame['sample'],
                                     liquid_arrows.parse_arrow_options((display or {}).get('arrows')))
        counts = {}
        for t in frame['targets']:
            counts[t['kind']] = counts.get(t['kind'], 0) + 1
        return {'sample': frame['sample'], 'timeSeconds': frame['timeSeconds'], 'groups': frame['groups'],
                'quantity': frame['quantity'], 'colormap': frame['colormap'],
                'targets': counts, 'painted': painted, 'unmatched': unmatched, **arrows}

    def _liquid_arrows(self, stage, sample, options):
        """Author (or remove) the flow arrows of the loaded liquid result at
        ``sample``; animated arrows move on Kit's update stream."""
        from . import liquid_arrows as la
        if options is None:
            self._stop_liquid_arrows(stage)
            return {'arrows': 0}
        import time
        spec = la.arrow_spec(self._liquid['result'].data, sample, options)
        motion = la.ArrowMotion(spec)
        la.author_arrows(stage, spec, motion.place(time.monotonic()))
        self._liquid_motion = motion
        if spec['animate'] and spec['arrows']:
            if self._liquid_arrow_sub is None:
                self._liquid_arrow_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
                    self._on_liquid_arrow_update, name='dcdtuni liquid flow arrows')
        else:
            self._liquid_arrow_sub = None
        return {'arrows': len(spec['arrows']), 'arrowBranchesNotDrawn': spec['skippedBranches']}

    def _on_liquid_arrow_update(self, _event):
        import time
        motion = self._liquid_motion
        now = time.monotonic()
        if motion is None or now - self._liquid_arrow_tick < 1 / 30:
            return
        self._liquid_arrow_tick = now
        from . import liquid_arrows as la
        try:
            la.update_arrows(omni.usd.get_context().get_stage(), la.ARROW_PATH, motion.place(now))
        except Exception as exc:  # noqa: BLE001 - a display helper must not break the update loop
            print(f'[dc.cfd_bridge] liquid arrows stopped: {exc}')
            self._liquid_arrow_sub = None

    def _stop_liquid_arrows(self, stage):
        from . import liquid_arrows as la
        self._liquid_arrow_sub = None
        self._liquid_motion = None
        if stage is not None:
            la.remove_arrows(stage)

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
        preview_controller = getattr(self, '_postprocess_preview_controller', None)
        if preview_controller is not None:
            preview_controller.invalidate()
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

    async def _volume_display(self, stage, field, domain, palette=None):
        return {'volume': await self._scene.show_volume_display(stage, field, domain, palette)}

    async def _surface_display(self, stage, field, domain, palette='coolwarm', hidden=()):
        return {'surface': await self._scene.show_surface_display(stage, field, domain, palette, hidden)}

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
        preview_controller = getattr(self, '_postprocess_preview_controller', None)
        if preview_controller is not None:
            preview_controller.invalidate()
        s = self._scene
        try:
            await s.remove_streamlines(stage)
            await s.remove_slice(stage)
            s.slice_visible = False
            await s.show_volume_display(stage, 'off')
            await s.show_surface_display(stage, 'off')
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

    async def _watch_scene_handoff(self):
        """Author the scene the server hands off (scene_handoff) as soon as its
        grant appears, so it is ready when the page's stream has connected."""
        from . import render_scene, scene_handoff
        runtime = os.environ.get('DC_CFD_RUNTIME_DIR', '')
        key = os.environ.get('DCDTUNI_OV_GRANT_KEY', '')
        if not runtime or not key:
            return
        loop = asyncio.get_event_loop()
        seen = None
        while not self._closing:
            await asyncio.sleep(0.25)
            stamp = scene_handoff.pending_stamp(runtime)
            if stamp is None or stamp == seen:
                continue
            seen = stamp
            try:
                payload, raw = await loop.run_in_executor(None, scene_handoff.read, runtime, key)
                if payload['sceneKey'] == self._render_scene_key and payload['projectHash'] == self._render_project_hash:
                    continue
                def decode():
                    packet = json.loads(raw.decode('utf-8'))
                    render_scene.validate_snapshot(packet)
                    return packet
                packet = await loop.run_in_executor(None, decode)
                stage = omni.usd.get_context().get_stage()
                generation = self._begin_display_change()
                self._render_scene_key = None  # until this one is authored
                task = asyncio.ensure_future(self._author_render_scene(
                    stage, packet, payload['projectHash'], 'handoff-' + payload['nonce'], generation, payload['sceneKey']))
                self._scene_handoff = {'sceneKey': payload['sceneKey'], 'projectHash': payload['projectHash'], 'task': task}
                print(f'[dc.cfd_bridge] scene handoff authoring primitives={len(packet["primitives"])}')
                await asyncio.wait([task])
                if task.exception() is not None:
                    print(f'[dc.cfd_bridge] scene handoff failed: {task.exception()}')
            except Exception as exc:  # noqa: BLE001 - a refused handoff leaves the page's own transfer
                print(f'[dc.cfd_bridge] scene handoff refused: {exc}')

    def _begin_display_change(self):
        """A new scene or display session: retire what belongs to the old one
        (as loadRenderScene and reuseRenderScene do) and return its generation."""
        self._display_generation += 1
        preview_controller = getattr(self, '_postprocess_preview_controller', None)
        if preview_controller is not None:
            preview_controller.invalidate()
        layout = getattr(self, '_owned_output_layout', None)
        if layout is not None:
            layout.invalidate()
        self._verified_result_controller.invalidate()
        self._trusted_result_context = None
        return self._display_generation

    async def _reuse_after_handoff(self, stage, view, request_id, task, scene_key, project_hash):
        """A reuse request that arrives while the handed-off scene it names is
        still being authored waits for it instead of being refused."""
        from . import render_scene
        await asyncio.shield(task)
        if (scene_key != self._render_scene_key or project_hash != self._render_project_hash
                or not stage.GetPrimAtPath(render_scene.ROOT)):
            raise RuntimeError('scene-not-held')
        return await self._reuse_render_scene(stage, view, request_id, self._begin_display_change())

    async def _reuse_render_scene(self, stage, view, request_id, generation):
        """Show the authored scene again for a new display session: what a
        scene load does around the authoring, without the authoring."""
        from . import render_scene
        async with self._display_commit_lock:
            if generation != self._display_generation:
                raise RuntimeError('scene reuse refused: a newer scene request superseded it')
            from .result_layers_usd import clear_result_layers
            clear_result_layers(stage)
            if stage.GetPrimAtPath(render_scene.OVERLAY_ROOT):
                stage.RemovePrim(render_scene.OVERLAY_ROOT)
            render_scene.apply_view(stage, view)
            self._last_scene_request_id = request_id
            self._scene_revision += 1
            self._last_ov_pick = None
            print(f'[dc.cfd_bridge] scene reused (revision {self._scene_revision}, no transfer)')
            return self._apply_render_view(stage, view)

    async def _author_render_scene(self, stage, packet, project_hash, request_id, generation, scene_key=None):
        import time as _time
        from . import render_scene
        timings = getattr(self, '_scene_load_timings', None) or {}
        self._scene_load_timings = None
        entered = _time.perf_counter()
        async with self._display_commit_lock:
            timings['lockSeconds'] = round(_time.perf_counter() - entered, 3)
            if generation != self._display_generation:
                raise RuntimeError('scene load refused: a newer scene request superseded it')
            # Resolve the owned texture cache before any live-stage write: a
            # misconfigured cache must leave the old scene, its display and its
            # result layers exactly as they were.
            from .owned_paths import render_texture_cache_path
            cache = render_texture_cache_path()
            if (self._display_project_uid is not None and self._scene is not None
                    and packet['project']['uid'] != self._display_project_uid):
                await self._clear_display(stage)
            # A newer request may arrive while the previous display is retiring.
            if generation != self._display_generation:
                raise RuntimeError('scene load refused: a newer scene request superseded it')
            from .result_layers_usd import clear_result_layers
            clear_result_layers(stage)
            timings['clearSeconds'] = round(_time.perf_counter() - entered - timings['lockSeconds'], 3)
            print(f'[dc.cfd_bridge] scene load begin primitives={len(packet["primitives"])}')
            authoring = _time.perf_counter()
            result = render_scene.author_scene(stage, packet, cache, validated=True)
            timings['authorSeconds'] = round(_time.perf_counter() - authoring, 3)
            print(f'[dc.cfd_bridge] scene load authored primitives={result["primitives"]} '
                  f'lights={result.get("lights")} retiredLights={result.get("retiredLights")}')
            # The files the stage binds: pool files themselves, or the owned cache
            # for embedded pictures (a receipt to check against materials.json).
            import json as _json  # tests run this method's source outside the module
            print('[dc.cfd_bridge] scene textures ' + _json.dumps(result.get('textureAssets', [])))
            # The scene's lights are the manifest's Kit rig (author_scene); its RTX
            # exposure is the same manifest section, as on the .cfdproj path.
            try:
                import carb.settings
                render_scene.apply_kit_exposure(carb.settings.get_settings())
                # The editor's lighting (view.lighting) over the manifest's defaults.
                render_scene.apply_lighting(stage, packet['view'].get('lighting'), carb.settings.get_settings())
            except Exception as exc:  # noqa: BLE001 - lighting must not fail a scene load
                print(f'[dc.cfd_bridge] render exposure not applied: {exc}')
            from .handlers import install_render_room_shell_paths
            self._render_room_shell_paths = tuple(result.get('roomShellPaths', ()))
            if self._scene is not None:
                install_render_room_shell_paths(self._scene, stage, self._render_room_shell_paths)
            self._render_uid_paths = result['uidPaths']
            self._render_primitive_ids = {p['id']: p['modelUid'] for p in packet['primitives']}
            self._render_project_hash = project_hash
            self._render_scene_key = scene_key
            self._last_scene_request_id = request_id
            self._scene_project_uid = packet['project']['uid']
            # The scene's materials were re-authored: saved originals are gone.
            self._liquid_saved = {}
            if self._liquid is not None:
                if packet['project']['uid'] != self._liquid['grant']['projectUid']:
                    self._liquid = None  # a different project: the result no longer applies
                    self._stop_liquid_arrows(stage)
                elif self._liquid['sample'] is not None:
                    self._paint_liquid(stage, self._liquid['sample'], self._liquid['domains'],
                                       self._liquid.get('display'))
            self._scene_revision += 1
            self._last_ov_pick = None
            from pxr import UsdGeom
            for path in ['/World/Geometry', '/World/FloorGrid']:
                old = stage.GetPrimAtPath(path)
                if old:
                    UsdGeom.Imageable(old).MakeInvisible()
            viewing = _time.perf_counter()
            view = self._apply_render_view(stage, packet['view'])
            timings['viewSeconds'] = round(_time.perf_counter() - viewing, 3)
            timings['totalSeconds'] = round(_time.perf_counter() - entered, 3)
            print('[dc.cfd_bridge] scene load timings ' + _json.dumps(timings))
            return view

    def _current_scene_context(self, stage):
        # The same active-stage UID/hash/revision producer used by signed
        # result transport, plus the owned lifecycle generation. No wire grant.
        return {**self._result_scene_identity(stage), 'displayGeneration':self._display_generation}

    def _ensure_grid_raster(self):
        if self._grid_raster is None:
            from .grid_raster import GridRasterCoordinator
            self._grid_raster = GridRasterCoordinator(self._current_scene_context,
                self._sample_grid_raster,lambda:self._grid_frame_revision,
                layout_lease=lambda: self._owned_output_layout.current_lease()
                    if getattr(self, '_owned_output_layout', None) is not None else None,
                native_sample=self._sample_native_grid)
        return self._grid_raster

    def _sample_grid_raster(self, stage):
        from . import render_scene
        from .grid_viewport import sample_grid_viewport
        from omni.kit.viewport.utility import get_active_viewport_and_window
        import omni.appwindow
        viewport, window = get_active_viewport_and_window()
        layout = getattr(self, '_owned_output_layout', None)
        owned = layout is not None and layout.enabled
        app = window.app_window if owned and window is not None else omni.appwindow.get_default_app_window()
        return sample_grid_viewport(viewport,stage,render_scene.CAMERA,
            self._current_scene_context(stage),window,app,output_layout=layout if owned else None)

    def _sample_native_grid(self, stage):
        from . import render_scene
        from .grid_viewport import sample_native_grid_viewport
        from omni.kit.viewport.utility import get_active_viewport_and_window
        viewport,window = get_active_viewport_and_window()
        return sample_native_grid_viewport(viewport,stage,render_scene.CAMERA,
            lambda: self._current_scene_context(stage),
            lambda: render_scene.read_view(stage),get_active_viewport_and_window,window)

    def _prepare_output_layout(self, stage):
        # Explicit lifecycle step. The sampler never installs/restores UI.
        layout = getattr(self, '_owned_output_layout', None)
        if layout is None or not layout.enabled:
            return
        from . import render_scene
        from omni.kit.viewport.utility import get_active_viewport_and_window
        viewport, window = get_active_viewport_and_window()
        selection = omni.usd.get_context().get_selection()
        layout.prepare(viewport, window, stage, render_scene.CAMERA,
            lambda: self._current_scene_context(stage),
            lambda: render_scene.read_view(stage, selection.get_selected_prim_paths()),
            get_active_viewport_and_window)

    def _observe_grid_viewport(self):
        from omni.kit.viewport.utility import get_active_viewport
        viewport = get_active_viewport()
        if viewport is None or viewport == self._grid_viewport_api:
            return
        for subscription in self._grid_subscriptions:
            subscription.destroy()
        self._grid_subscriptions = []
        self._grid_viewport_api = viewport
        def frame_changed(*_args):
            self._grid_frame_revision += 1
            if self._grid_raster is not None:
                self._grid_raster.notify()
        def view_changed(*_args):
            if self._grid_raster is not None:
                self._grid_raster.notify()
        self._grid_subscriptions = [viewport.subscribe_to_frame_change(frame_changed),
            viewport.subscribe_to_view_change(view_changed),
            viewport.subscribe_to_render_settings_change(view_changed)]

    def _apply_render_view(self, stage, view):
        from . import render_scene
        if view.get('camera'):
            from omni.kit.viewport.utility import get_active_viewport
            viewport = get_active_viewport()
            if viewport:
                # Render the common camera at the actual native widget size.
                viewport.fill_frame = True
                viewport.camera_path = render_scene.CAMERA
        ctx = omni.usd.get_context()
        from pxr import UsdGeom
        # Hidden source children must not contaminate a later native Shift hit.
        visible_paths = {}
        for uid in view['selectedUids']:
            visible_paths[uid] = []
            for path in self._render_uid_paths.get(uid, ()):
                prim = stage.GetPrimAtPath(path)
                if prim and UsdGeom.Imageable(prim).ComputeVisibility() != UsdGeom.Tokens.invisible:
                    visible_paths[uid].append(path)
        paths = [path for children in visible_paths.values() for path in children]
        self._suppress_ov_pick = True
        try:
            ctx.get_selection().set_selected_prim_paths(paths, True)
            # Native notifications can arrive after this call returns. Remember
            # the complete successfully applied UID set, not its first primitive.
            self._last_ov_pick = (self._scene_revision, tuple(sorted(
                uid for uid, children in visible_paths.items() if children)))
        finally:
            self._suppress_ov_pick = False
        self._prepare_output_layout(stage)
        self._observe_grid_viewport()
        grid = self._ensure_grid_raster()
        grid.offer(stage,view)
        return {'view': view, 'gridDisplay': grid.display_receipt(stage,view), 'primitives': sum(len(x) for x in self._render_uid_paths.values()),
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
                    grid = payload.get('gridDisplay')
                    if type(grid) is dict and 'nativeGeometry' in grid:
                        # Queued ACK snapshots cannot retain a now-stale native
                        # success. Preserve their original encoded status/reason.
                        current = dict(grid)
                        current.pop('nativeGeometry', None)
                        try:
                            stage = omni.usd.get_context().get_stage()
                            coordinator = self._grid_raster
                            fresh = coordinator.display_receipt(stage,payload.get('view')) if coordinator else {}
                            if 'nativeGeometry' in fresh:
                                current['nativeGeometry'] = fresh['nativeGeometry']
                        except Exception:
                            pass
                        payload['gridDisplay'] = current
                    # Opt-in shape flags immediately before the original bus
                    # push; no command, readback, proof repair or ACK refresh.
                    try:
                        from .grid_raster import _native_grid_receipt_diagnostic
                        _native_grid_receipt_diagnostic('flush-send',payload.get('gridDisplay'))
                    except Exception:
                        pass  # Diagnostic import/sink failure cannot suppress ACK.
                    # Carb drops None dictionary entries and turns empty arrays
                    # into objects. Preserve real views and freshly revalidated
                    # native receipts as JSON; the receiver reconstructs no fields.
                    wire_grid = payload.get('gridDisplay')
                    if (type(wire_grid) is dict and 'nativeGeometry' in wire_grid
                            or type(payload.get('view')) is dict):
                        try:
                            import json
                            wire = {'renderAckJSON': json.dumps(
                                payload, allow_nan=False, ensure_ascii=False,
                                separators=(',', ':'))}
                            app.get_message_bus_event_stream().push(event, payload=wire)
                        except Exception:
                            # No legacy fallback can carry a failed view/proof.
                            # A failed encode/send must not discard later ACKs.
                            continue
                    else:
                        app.get_message_bus_event_stream().push(event, payload=payload)
        finally:
            self._ack_flushing = False

    def _on_stage_event(self, event):
        if event.type != int(omni.usd.StageEventType.SELECTION_CHANGED):
            return
        self._emit_source_pick()

    def _trace_source_selection(self, phase, paths=None, uids=None, refused_index=None):
        trace = getattr(self, '_source_selection_trace', None)
        if trace is None or not trace.enabled:
            return
        trace.observe(phase, paths=paths, uids=uids, refused_index=refused_index,
                      cached_identity=self._last_ov_pick, scene_revision=self._scene_revision,
                      project_hash=self._render_project_hash)

    def _emit_source_pick(self):
        """Publish the complete visible source-object selection, including clear.

        Kit Shift adds USD primitives. React owns whole-object highlighting, so
        deduplicate their acknowledged model UIDs without discarding later hits.
        Native Ctrl primitive inversion is not inferred to be a UID toggle.
        """
        if self._suppress_ov_pick or not self._render_project_hash:
            self._trace_source_selection('suppressed-or-unbound')
            return
        from pxr import UsdGeom
        ctx = omni.usd.get_context()
        stage = ctx.get_stage()
        if stage is None:
            self._trace_source_selection('no-stage')
            return
        paths = ctx.get_selection().get_selected_prim_paths()
        if len(paths) > 65536:
            self._trace_source_selection('path-cap', paths)
            return
        selected = {}
        for path_index, path in enumerate(paths):
            # A mixed/unknown/hidden native selection is unresolved, never a clear
            # or a partial snapshot of the remaining valid objects.
            if not isinstance(path, str) or not path.startswith('/World/RenderScene/Geometry/'):
                self._trace_source_selection('foreign-path', paths, selected, path_index)
                return
            prim = stage.GetPrimAtPath(path)
            if not prim or UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
                self._trace_source_selection('missing-or-hidden-prim', paths, selected, path_index)
                return
            prim_id = prim.GetCustomDataByKey('dcdtuni:primitiveId')
            model_uid = prim.GetCustomDataByKey('dcdtuni:modelUid')
            if (not isinstance(prim_id, str) or not 1 <= len(prim_id) <= 1024
                    or not isinstance(model_uid, str) or not 1 <= len(model_uid) <= 256
                    or self._render_primitive_ids.get(prim_id) != model_uid
                    or path not in self._render_uid_paths.get(model_uid, ())):
                self._trace_source_selection('binding-refused', paths, selected, path_index)
                return
            selected[model_uid] = min(prim_id, selected.get(model_uid, prim_id))
        if len(selected) > 1024:
            self._trace_source_selection('uid-cap', paths, selected)
            return
        identity = (self._scene_revision, tuple(sorted(selected)))
        if identity == self._last_ov_pick:
            self._trace_source_selection('dedupe', paths, selected)
            return
        payload = {
            'schema': 'dcdtuni.ov-edit/v1',
            'sceneRevision': self._scene_revision,
            'projectHash': self._render_project_hash,
            'coordinateSystem': {'upAxis': 'Y', 'handedness': 'right', 'metersPerUnit': 1},
            'action': {'type': 'selection'},
            # Keep native first-seen order so a Shift-appended object remains
            # the common driver's last (active) UID; set identity stays order-free.
            'selection': [{'primId': prim_id, 'modelUid': uid} for uid, prim_id in selected.items()],
        }
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 65536:
            self._trace_source_selection('payload-cap', paths, selected)
            return
        self._last_ov_pick = identity
        signal = carb.events.type_from_string('dcdtuni:ovEditEvent')
        omni.kit.app.get_app().get_message_bus_event_stream().push(signal, payload=payload)
        self._trace_source_selection('bus-push-returned', paths, selected)

    def _emit_postprocess_preview(self, payload):
        # Separate display readback: never a source-object editable selection.
        signal = carb.events.type_from_string('dcdtuni:postprocessPreviewEvent')
        omni.kit.app.get_app().get_message_bus_event_stream().push(signal, payload=payload)

    def _route_render_command(self, command, message):
        from . import render_scene
        request_id = ''
        try:
            # Bound every getViewState before JSON parsing; other commands unchanged.
            if command in ('setCommonPostprocessPreviews','retireCommonPostprocessPreviews') and (not isinstance(message,str) or len(message)>32768 or len(message.encode('utf-8'))>32768):
                raise ValueError('Shared preview command too large')
            if command in ('getViewState','querySourcePick') and (not isinstance(message,str) or len(message)>1024 or len(message.encode('utf-8'))>1024):
                return
            args = json.loads(message)
            if not isinstance(args, dict) or not isinstance(args.get('requestId'), str):
                raise ValueError('Render command requires requestId')
            request_id = args['requestId']
            allowed = {
                'beginRenderScene': {'requestId', 'chunks'},
                'renderSceneChunk': {'requestId', 'index', 'data'},
                'renderSceneProgress': {'requestId', 'sceneRequestId', 'received'},
                'loadRenderScene': {'requestId'},
                'reuseRenderScene': {'requestId', 'sceneKey', 'projectHash', 'view'},
                'beginRenderOverlay': {'requestId', 'chunks'},
                'renderOverlayChunk': {'requestId', 'index', 'data'},
                'renderOverlayProgress': {'requestId', 'overlayRequestId', 'received'},
                'loadRenderOverlay': {'requestId'},
                'beginRenderResultLayers': {'requestId', 'chunks'},
                'renderResultLayersChunk': {'requestId', 'index', 'data'},
                'renderResultLayersProgress': {'requestId', 'resultRequestId', 'received'},
                'loadRenderResultLayers': {'requestId'},
                'loadVerifiedRenderResultLayers': {'requestId', 'grant'},
                'clearRenderResultLayers': {'requestId'},
                'setCommonPostprocessPreviews': {'requestId', 'packet'},
                'retireCommonPostprocessPreviews': {'requestId', 'binding'},
                'updateViewState': {'requestId', 'view'},
                'getViewState': {'requestId'},
                'querySourcePick': {'requestId','pick'},
                'setSlice': {'requestId', 'visible', 'origin', 'normal', 'radius',
                             'field', 'domain'},
                'getSliceState': {'requestId'},
                'loadResultDataset': {'requestId', 'grant'},
                'setVolumeDisplay': {'requestId', 'field', 'domain'},
                'setSurfaceDisplay': {'requestId', 'field', 'domain', 'palette', 'hidden'},
                'loadLiquidResult': {'requestId', 'grant'},
                'setLiquidFrame': {'requestId', 'sample', 'domains'},
                'clearLiquidColours': {'requestId'},
            }
            if set(args) != allowed[command] and not (
                    (command in ('beginRenderScene', 'beginRenderOverlay') and set(args) == {'requestId', 'chunks', 'encoding'}) or
                    (command == 'loadRenderScene' and set(args) == {'requestId', 'projectHash'}) or
                    (command == 'loadRenderScene' and set(args) == {'requestId', 'projectHash', 'sceneKey'}) or
                    (command == 'clearRenderResultLayers' and set(args) == {'requestId', 'displayGeneration'}) or
                    (command == 'setVolumeDisplay' and set(args) == {'requestId', 'field', 'domain', 'palette'}) or
                    (command == 'setSlice' and set(args) == {'requestId', 'visible', 'origin', 'normal', 'radius',
                                                             'field', 'domain', 'palette', 'showDisc'}) or
                    (command == 'setLiquidFrame' and set(args) == {'requestId', 'sample', 'domains', 'display'}) or
                    (command == 'getViewState' and set(args) == {'requestId', 'diagnostics'}) or
                    (command == 'getViewState' and set(args) == {'requestId', 'purpose'}
                     and args['purpose'] == 'camera-handoff')):
                raise ValueError('Unexpected or missing render command fields')
            if command in {'beginRenderResultLayers', 'renderResultLayersChunk',
                           'renderResultLayersProgress', 'loadRenderResultLayers'}:
                # A signed context must never authorize arbitrary later browser
                # scalar packets. Only the owned server canonical artifact is loaded.
                raise ValueError('Result layers require a verified server canonical grant')
            if command == 'beginRenderScene':
                self._render_transfer.begin(request_id, args['chunks'], args.get('encoding'))
                print(f'[dc.cfd_bridge] scene begin chunks={args["chunks"]} encoding={args.get("encoding")}')
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
                self._overlay_transfer.begin(request_id, args['chunks'], args.get('encoding'))
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
            if command == 'querySourcePick':
                from .render_output_observations import reviewed_runtime_getters
                from omni.kit.viewport.utility import get_active_viewport_and_window
                from omni import appwindow
                from omni.ui import Workspace
                import carb.settings
                def runtime():
                    api,window=get_active_viewport_and_window()
                    return (api,window,appwindow.get_default_app_window(),Workspace,
                        reviewed_runtime_getters(),carb.settings.get_settings(),get_active_viewport_and_window)
                def source_context():
                    return dict(projectUid=self._scene_project_uid,sceneRevision=self._scene_revision,
                        projectHash=self._render_project_hash,displayGeneration=self._display_generation,
                        uidPaths=self._render_uid_paths,primitiveIds=self._render_primitive_ids)
                async def query():
                    result=await self._source_pick_controller.query(args['pick'],runtime,source_context,
                        lambda:render_scene.read_view(omni.usd.get_context().get_stage(),()))
                    return dict(sourcePickJSON=json.dumps(result,allow_nan=False,separators=(',',':')))
                self._ack_after(request_id,command,query())
                return
            if command == 'setCommonPostprocessPreviews':
                from .postprocess_preview_viewport import NativePreviewFactory
                ack = self._postprocess_preview_controller.install(args['packet'], NativePreviewFactory())
                self._render_ack(request_id, ok=True, postprocessPreviews=ack)
                return
            if command == 'retireCommonPostprocessPreviews':
                retired = self._postprocess_preview_controller.retire(args['binding'])
                self._render_ack(request_id, ok=True, retiredPostprocessPreviews=retired)
                return
            if command == 'loadVerifiedRenderResultLayers':
                from . import display_grant
                import time as _time
                granted = display_grant.verify(args['grant'],
                    os.environ.get('DCDTUNI_OV_GRANT_KEY', ''), now=_time.time(),
                    roots=display_grant.roots_from_env(os.environ.get('DC_CFD_DATASET_ROOTS')),
                    kind=('rendererMesh' if args['grant'].get('payload',{}).get('kind')=='rendererMesh' else 'rendererResult'))
                generation = self._display_generation
                ticket = self._verified_result_controller.accept(
                    granted, self._result_scene_identity(stage), generation)
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                self._trusted_result_context = {'source': granted['source'], 'timeSeconds': granted['timeSeconds']}
                self._ack_after(request_id, command, self._load_verified_result(stage, ticket))
                return
            if command == 'clearRenderResultLayers':
                serial = self._verified_result_controller.invalidate(display_generation=args.get('displayGeneration'))
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                self._trusted_result_context = None
                self._ack_after(request_id, command,
                    self._clear_verified_result(stage, serial, self._display_generation))
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
            if command == 'reuseRenderScene':
                from . import render_scene
                scene_key, project_hash = args['sceneKey'], args['projectHash']
                if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{64}', x) for x in (scene_key, project_hash)):
                    raise ValueError('Invalid scene identity')
                if (self._render_scene_key is None or scene_key != self._render_scene_key
                        or project_hash != self._render_project_hash
                        or not stage.GetPrimAtPath(render_scene.ROOT)):
                    handoff = getattr(self, '_scene_handoff', None)
                    if (handoff is not None and not handoff['task'].done() and handoff['sceneKey'] == scene_key
                            and handoff['projectHash'] == project_hash):
                        render_scene.validate_view(args['view'])
                        self._ack_after(request_id, command, self._reuse_after_handoff(
                            stage, args['view'], request_id, handoff['task'], scene_key, project_hash))
                        return
                    self._render_ack(request_id, ok=False, error='scene-not-held')
                    return
                render_scene.validate_view(args['view'])
                self._display_generation += 1
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                layout = getattr(self, '_owned_output_layout', None)
                if layout is not None:
                    layout.invalidate()
                self._verified_result_controller.invalidate()
                self._trusted_result_context = None
                self._ack_after(request_id, command, self._reuse_render_scene(
                    stage, args['view'], request_id, self._display_generation))
                return
            if command == 'loadRenderScene':
                import time as _time
                started = _time.perf_counter()
                packet = self._render_transfer.finish(request_id)
                self._scene_load_timings = {'finishSeconds': round(_time.perf_counter() - started, 3)}
                project_hash = args.get('projectHash')
                if project_hash is not None and (not isinstance(project_hash, str) or
                        not re.fullmatch(r'[0-9a-f]{64}', project_hash)):
                    raise ValueError('Invalid project content hash')
                scene_key = args.get('sceneKey')
                if scene_key is not None and (not isinstance(scene_key, str) or
                        not re.fullmatch(r'[0-9a-f]{64}', scene_key)):
                    raise ValueError('Invalid scene key')
                self._display_generation += 1
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                layout = getattr(self, '_owned_output_layout', None)
                if layout is not None:
                    layout.invalidate()
                self._verified_result_controller.invalidate()
                self._trusted_result_context = None
                self._render_scene_key = None  # until this one is authored
                self._ack_after(request_id, command, self._author_render_scene(
                    stage, packet, project_hash, request_id, self._display_generation, scene_key))
                return
            elif command == 'setSlice':
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                from . import handlers, parsers
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                parsed = parsers.parse_set_slice(message)
                # Adopt parameters and author the cut inside the commit barrier.
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._set_slice(stage, parsed)))
                return
            elif command == 'getSliceState':
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                # "Read back the viewport drag": adopt a dragged preview disc,
                # re-cut there, and report the state Kit now holds.
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._slice_readback(stage)))
                return
            elif command == 'loadLiquidResult':
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
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
                self._render_ack(request_id, ok=True,
                                 liquidFrame=self._paint_liquid(stage, sample, domains, args.get('display')))
                return
            elif command == 'clearLiquidColours':
                from . import render_scene as _rs
                cleared = len(self._liquid_saved)
                _rs.restore_colours(stage, self._liquid_saved)
                self._liquid = None
                self._stop_liquid_arrows(stage)
                self._render_ack(request_id, ok=True, cleared=cleared)
                return
            elif command == 'setVolumeDisplay':
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
                from . import parsers
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                parsed = parsers.parse_set_volume_display_palette(message)
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._volume_display(stage, **parsed)))
                return
            elif command == 'setSurfaceDisplay':
                from . import parsers
                if self._scene is None:
                    raise ValueError('Kit scene is not initialised yet')
                parsed = parsers.parse_set_surface_display(message)
                self._ack_after(request_id, command, self._display_mutation(
                    self._display_generation, lambda: self._surface_display(stage, **parsed)))
                return
            elif command == 'loadResultDataset':
                preview_controller = getattr(self, '_postprocess_preview_controller', None)
                if preview_controller is not None:
                    preview_controller.invalidate()
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
                self._ensure_grid_raster().validate_binding(stage,args['view'])
                view = render_scene.apply_view(stage,args['view'],
                    self._ensure_grid_raster().carrier_contract(stage,args['view']))
            else:
                if 'diagnostics' in args:
                    from .display_diagnostics import parse_request
                    from .grid_viewport import sample_grid_viewport
                    parse_request(message)
                    import carb.settings
                    from omni.kit.viewport.utility import get_active_viewport_and_window
                    from omni import appwindow
                    def current_context():
                        return {'stage': omni.usd.get_context().get_stage(),
                            'sceneRevision': self._scene_revision, 'projectHash': self._render_project_hash,
                            'displayGeneration': self._display_generation, 'uidPaths': self._render_uid_paths,
                            'primitives': sum(len(x) for x in self._render_uid_paths.values())}
                    from .render_output_observations import sample_render_output_observations, reviewed_runtime_getters
                    def output_observations(api, window, app_window, **pins):
                        return sample_render_output_observations(api, window, app_window,
                            reviewed_runtime_getters(), paired_viewport=get_active_viewport_and_window, **pins)
                    from .active_material_parameters import sample as material_parameters
                    reply = self._bounded_display_readback.read(message, current_context,
                        lambda: render_scene.read_view(stage, ctx.get_selection().get_selected_prim_paths()),
                        get_active_viewport_and_window, carb.settings.get_settings(),
                        appwindow.get_default_app_window, sample_grid_viewport, output_sampler=output_observations,
                        material_sampler=material_parameters)
                    if reply is not None:
                        self._render_ack(reply.pop('requestId'), **reply)
                    return
                selection_paths = ctx.get_selection().get_selected_prim_paths()
                view = render_scene.read_view(stage, selection_paths)
                if args.get('purpose') == 'camera-handoff':
                    # This live camera/scene transaction cannot authorize grid
                    # appearance. Ordinary reads still verify the full receipt.
                    self._render_ack(request_id, ok=True, view=view,
                                     primitives=sum(len(x) for x in self._render_uid_paths.values()),
                                     sceneRevision=self._scene_revision, projectHash=self._render_project_hash,
                                     sceneRequestId=self._last_scene_request_id)
                    self._trace_source_selection('state-query', selection_paths, view.get('selectedUids'))
                    return
                # Idempotent state query: also the scene identity a scene ack
                # carries, so a lost scene ack is recoverable (ovProtocol).
                self._render_ack(request_id, ok=True, view=view,
                                 primitives=sum(len(x) for x in self._render_uid_paths.values()),
                                 sceneRevision=self._scene_revision, projectHash=self._render_project_hash,
                                 sceneRequestId=self._last_scene_request_id,
                                 gridDisplay=self._grid_raster.display_receipt(stage,view) if self._grid_raster else {'phase':'pending','reason':'raster-unobserved'})
                self._trace_source_selection('state-query', selection_paths, view.get('selectedUids'))
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
