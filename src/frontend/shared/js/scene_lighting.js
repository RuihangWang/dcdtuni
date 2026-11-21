/**
 * scene_lighting.js — the lighting both editors use, from one definition.
 *
 * Everything comes from the `lighting` section of textures/materials.json
 * (read through js/material_library.js), which the Omniverse side reads too:
 *
 * - the renderer's tone mapping, exposure and output colour space;
 * - the camera-parented light kit (a VTK-style key / fill / head / back pair,
 *   parented to the camera so shading stays view-relative and a rotated model
 *   keeps its form); intensities are multiplied by pi because three r155+ uses
 *   physical light units;
 * - a dim ambient term;
 * - a pre-filtered room environment, which is what the PBR surfaces reflect.
 *   Without it a metal lit only by directional lights has nothing to mirror
 *   and reads as flat dark grey.
 *
 * - named presets (`lighting.presets`) that scale the rig as a whole, and an
 *   optional world-fixed oblique shadow light (`lighting.shadow`), the light
 *   Kit casts its shadows from too, so both renderers drop the same shadows. Kit applies
 *   the same presets to its dome, key and fill, so the two renderers move together.
 *
 * `THREE` is passed in, not read off a global: the legacy page has a global
 * build, React imports the module. The room is a port of three r184's
 * examples/jsm/environments/RoomEnvironment.js (MIT, after model-viewer's
 * EnvironmentScene), so the legacy page — which has no module loader — gets
 * the same environment as React.
 */
(function (root, factory) {
    function library() {
        var lib = (root && root.materialLibrary) || null;
        if (!lib && typeof module === 'object' && module.exports && typeof require === 'function') lib = require('./material_library.js');
        if (!lib) throw new Error('scene_lighting.js needs js/material_library.js loaded first');
        return lib;
    }
    var api = factory(library);
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.sceneLighting = api;
})(typeof globalThis !== 'undefined' ? globalThis : (typeof window !== 'undefined' ? window : this), function (library) {
    'use strict';

    var TONE_MAPPING = {
        'aces-filmic': 'ACESFilmicToneMapping',
        'agx': 'AgXToneMapping',
        'neutral': 'NeutralToneMapping',
        'none': 'NoToneMapping',
    };

    function lighting(options) {
        return (options && options.lighting) || library().lighting();
    }

    /** Tone mapping, exposure and output colour space, from the manifest. */
    function configureRenderer(THREE, renderer, options) {
        var l = lighting(options);
        renderer.toneMapping = THREE[TONE_MAPPING[l.toneMapping] || 'ACESFilmicToneMapping'];
        renderer.toneMappingExposure = l.exposure;
        renderer.outputColorSpace = THREE.SRGBColorSpace;
    }

    function areaLightMaterial(THREE, intensity) {
        // emissive only (three.js #31348)
        return new THREE.MeshLambertMaterial({ color: 0x000000, emissive: 0xffffff, emissiveIntensity: intensity });
    }

    /** The room the environment map is baked from (three r184 RoomEnvironment). */
    function roomEnvironmentScene(THREE) {
        var scene = new THREE.Scene();
        scene.name = 'RoomEnvironment';
        scene.position.y = -3.5;
        var geometry = new THREE.BoxGeometry();
        geometry.deleteAttribute('uv');
        var mainLight = new THREE.PointLight(0xffffff, 900, 28, 2);
        mainLight.position.set(0.418, 16.199, 0.300);
        scene.add(mainLight);
        var room = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ side: THREE.BackSide }));
        room.position.set(-0.757, 13.219, 0.717);
        room.scale.set(31.713, 28.305, 28.591);
        scene.add(room);
        var boxes = new THREE.InstancedMesh(geometry, new THREE.MeshStandardMaterial(), 6);
        var transform = new THREE.Object3D();
        [
            [[-10.906, 2.009, 1.846], -0.195, [2.328, 7.905, 4.651]],
            [[-5.607, -0.754, -0.758], 0.994, [1.970, 1.534, 3.955]],
            [[6.167, 0.857, 7.803], 0.561, [3.927, 6.285, 3.687]],
            [[-2.017, 0.018, 6.124], 0.333, [2.002, 4.566, 2.064]],
            [[2.291, -0.756, -2.621], -0.286, [1.546, 1.552, 1.496]],
            [[-2.193, -0.369, -5.547], 0.516, [3.875, 3.487, 2.986]],
        ].forEach(function (b, i) {
            transform.position.set(b[0][0], b[0][1], b[0][2]);
            transform.rotation.set(0, b[1], 0);
            transform.scale.set(b[2][0], b[2][1], b[2][2]);
            transform.updateMatrix();
            boxes.setMatrixAt(i, transform.matrix);
        });
        scene.add(boxes);
        [
            [50, [-16.116, 14.37, 8.208], [0.1, 2.428, 2.739]],    // -x right
            [50, [-16.109, 18.021, -8.207], [0.1, 2.425, 2.751]],  // -x left
            [17, [14.904, 12.198, -1.832], [0.15, 4.265, 6.331]],  // +x
            [43, [-0.462, 8.89, 14.520], [4.38, 5.441, 0.088]],    // +z
            [20, [3.235, 11.486, -12.541], [2.5, 2.0, 0.1]],       // -z
            [100, [0.0, 20.0, 0.0], [1.0, 0.1, 1.0]],              // +y
        ].forEach(function (a) {
            var panel = new THREE.Mesh(geometry, areaLightMaterial(THREE, a[0]));
            panel.position.set(a[1][0], a[1][1], a[1][2]);
            panel.scale.set(a[2][0], a[2][1], a[2][2]);
            scene.add(panel);
        });
        return scene;
    }

    function disposeScene(scene) {
        var resources = new Set();
        scene.traverse(function (o) {
            if (o.isMesh) { resources.add(o.geometry); resources.add(o.material); }
        });
        resources.forEach(function (r) { r.dispose(); });
    }

    /**
     * Bake the room into a PMREM and set it as `scene.environment` at the
     * manifest's intensity; returns its disposer. Never throws: without a
     * working WebGL context (jsdom, a lost context) the scene simply keeps no
     * environment.
     */
    function installEnvironment(THREE, renderer, scene, options) {
        var env = lighting(options).environment;
        var pmrem = null, room = null;
        try {
            pmrem = new THREE.PMREMGenerator(renderer);
            room = roomEnvironmentScene(THREE);
            var target = pmrem.fromScene(room, env.blurSigma);
            scene.environment = target.texture;
            scene.environmentIntensity = env.intensity;
            return function () {
                if (scene.environment === target.texture) scene.environment = null;
                target.dispose();
            };
        } catch (err) {
            if (typeof console !== 'undefined') console.warn('[lighting] environment unavailable; PBR surfaces get direct light only', err);
            return function () {};
        } finally {
            if (room) disposeScene(room);
            if (pmrem) pmrem.dispose();
        }
    }

    var UNIT_PRESET = { exposure: 1, environment: 1, key: 1, fill: 1, rim: 1, ambient: 1 };

    var SHADOW_TYPE = { 'basic': 'BasicShadowMap', 'pcf': 'PCFShadowMap', 'pcf-soft': 'PCFSoftShadowMap' };
    /** Below this a transparent surface casts no shadow (Kit: render_scene.SHADOW_MIN_OPACITY). */
    var SHADOW_MIN_OPACITY = 0.6;

    /**
     * Unit vector from the scene toward a Kit DistantLight whose USD
     * xformOp:rotateXYZ is `degrees`: the light shines along its local -Z,
     * rotated about X, then Y, then Z (Y-up, as both scenes are). The shadow
     * light's [-49, -43, 0] gives [-0.4474, 0.7547, 0.4798], 49 degrees up.
     */
    function kitLightDirection(degrees) {
        var r = degrees.map(function (d) { return d * Math.PI / 180; });
        var v = [0, 0, -1];
        var c = Math.cos(r[0]), s = Math.sin(r[0]);
        v = [v[0], c * v[1] - s * v[2], s * v[1] + c * v[2]];
        c = Math.cos(r[1]); s = Math.sin(r[1]);
        v = [c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2]];
        c = Math.cos(r[2]); s = Math.sin(r[2]);
        v = [c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]];
        return [-v[0], -v[1], -v[2]];
    }

    /** The preset factor a camera light of this role takes: key; fill and head; the back pair as rim. */
    function roleFactor(preset, role) {
        if (role === 'key') return preset.key;
        if (role === 'fill' || role === 'head') return preset.fill;
        return preset.rim;
    }

    /** A mesh that should not take part in shadows: helpers, overlays, see-through panels. */
    function shadowExempt(o) {
        var u = o.userData || {};
        if (u.isHelper || u.isAnchorMarker || u.isResizeHandle || u.isMoveGizmo || u.rotateGizmoAxis) return true;
        var m = Array.isArray(o.material) ? o.material[0] : o.material;
        if (!m || m.isShaderMaterial || m.isMeshBasicMaterial || m.isLineBasicMaterial || m.isPointsMaterial) return true;
        return false;
    }

    /**
     * Configure the renderer and light the scene. The camera must already be
     * in the scene graph (the kit is parented to it). Returns
     * `{ lights, baseIntensities, environmentIntensity, presets, setScale,
     *    setEnabled, setPreset, setShadows, fitShadows, dispose }`.
     */
    function install(THREE, renderer, scene, camera, options) {
        var l = lighting(options);
        configureRenderer(THREE, renderer, options);
        var presets = l.presets || { standard: UNIT_PRESET };
        var preset = presets.standard || UNIT_PRESET;
        var shadowSpec = l.shadow || null;
        var roles = [];
        var lights = [];
        l.cameraLights.forEach(function (spec) {
            roles.push(spec.role);
            var light = new THREE.DirectionalLight(spec.color, spec.intensity * Math.PI);
            light.name = 'lightkit-' + spec.role;
            light.position.set(spec.direction[0], spec.direction[1], spec.direction[2]);
            camera.add(light);
            camera.add(light.target);
            lights.push(light);
        });
        var ambient = new THREE.AmbientLight(l.ambient.color, l.ambient.intensity * Math.PI);
        ambient.name = 'lightkit-ambient';
        scene.add(ambient);
        lights.push(ambient);
        roles.push('ambient');
        var base = lights.map(function (light) { return light.intensity; });
        var keyIndex = roles.indexOf('key');
        var envBase = l.environment.intensity;
        var disposeEnvironment = installEnvironment(THREE, renderer, scene, options);
        var scale = 1, enabled = true, shadowsOn = false, shadowLight = null, shadowRoot = null;
        // The caller's say on which meshes cast (the room shell does not: an
        // oblique light through walls and ceiling would shade the whole room).
        var casterFilter = null;
        // Lights off keeps the geometry readable on a flat ambient term (the
        // manifest's lightsOffIntensity) while result layers go unlit.
        function apply() {
            var k = enabled ? scale : 0;
            var share = shadowsOn && shadowLight && shadowSpec ? shadowSpec.share : 0;
            lights.forEach(function (light, i) {
                if (light === ambient) {
                    light.intensity = enabled ? base[i] * preset.ambient * k : l.ambient.lightsOffIntensity * Math.PI;
                    return;
                }
                // With shadows on, the world-fixed light takes over part of the
                // camera key, so the shadows fall from light that is really there.
                light.intensity = base[i] * roleFactor(preset, roles[i]) * k * (i === keyIndex ? 1 - share : 1);
            });
            // The shadow light is lit above its share and the environment
            // dimmed while it casts, or the shadow is lost in the environment's
            // light (lighting.shadow: gain, environment).
            var gain = share ? shadowSpec.gain : 1;
            if (shadowLight) shadowLight.intensity = keyIndex >= 0 ? base[keyIndex] * preset.key * k * share * gain : 0;
            if (scene.environment) scene.environmentIntensity = envBase * preset.environment * k * (share ? shadowSpec.environment : 1);
            renderer.toneMappingExposure = l.exposure * preset.exposure;
        }
        function markShadows(root, on) {
            root.traverse(function (o) {
                if (!o.isMesh) return;
                var exempt = shadowExempt(o);
                var m = Array.isArray(o.material) ? o.material[0] : o.material;
                var seeThrough = !!(m && m.transparent && m.opacity < SHADOW_MIN_OPACITY);
                o.castShadow = on && !exempt && !seeThrough && (!casterFilter || casterFilter(o) !== false);
                o.receiveShadow = on && !exempt;
            });
        }
        /** Aim the shadow light at `root` and size its frustum to the bounds. */
        function fitShadows(root) {
            if (root) shadowRoot = root;
            if (!shadowsOn || !shadowLight || !shadowRoot) return;
            markShadows(shadowRoot, true);
            var box = new THREE.Box3().setFromObject(shadowRoot);
            if (box.isEmpty()) return;
            var center = box.getCenter(new THREE.Vector3());
            var radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.5);
            var d = kitLightDirection(shadowSpec.rotateXYZ);
            var dir = new THREE.Vector3(d[0], d[1], d[2]).normalize();
            shadowLight.position.copy(center).addScaledVector(dir, radius * 2);
            shadowLight.target.position.copy(center);
            shadowLight.target.updateMatrixWorld();
            var cam = shadowLight.shadow.camera;
            cam.left = -radius; cam.right = radius; cam.top = radius; cam.bottom = -radius;
            cam.near = Math.max(radius * 0.01, 0.01); cam.far = radius * 4;
            cam.updateProjectionMatrix();
            refreshShadows();
        }
        /**
         * Draw the shadow map on the next frame. It is not redrawn every frame
         * (autoUpdate off): the light is world-fixed, so only a change of the
         * scene changes it -- a rebuild calls fitShadows, a batch rebuild this.
         */
        function refreshShadows() {
            if (!shadowsOn) return;
            renderer.shadowMap.needsUpdate = true;
            if (shadowLight) shadowLight.shadow.needsUpdate = true;
        }
        function recompile() {
            scene.traverse(function (o) {
                if (!o.material) return;
                (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) { m.needsUpdate = true; });
            });
        }
        return {
            lights: lights,
            baseIntensities: base,
            environmentIntensity: envBase,
            /** The preset ids the manifest offers, 'standard' first. */
            presets: Object.keys(presets).sort(function (a, b) { return a === 'standard' ? -1 : b === 'standard' ? 1 : 0; }),
            /** Scale every light and the environment (the viewport's light slider). */
            setScale: function (value) { scale = value; apply(); },
            /** Lights off / on, keeping the slider's scale. */
            setEnabled: function (on) { enabled = !!on; apply(); },
            /** One of `presets`; an unknown id falls back to 'standard'. */
            setPreset: function (id) { preset = presets[id] || presets.standard || UNIT_PRESET; apply(); },
            /**
             * Shadows on / off: a world-fixed light from `lighting.shadow`
             * casting onto every mesh under `root` that a shadow belongs to.
             */
            setShadows: function (on, root) {
                var next = !!on && !!shadowSpec;
                if (root) shadowRoot = root;
                if (next && !shadowLight) {
                    var keySpec = l.cameraLights[keyIndex] || l.cameraLights[0];
                    shadowLight = new THREE.DirectionalLight(keySpec.color, 0);
                    shadowLight.name = 'lightkit-shadow';
                    shadowLight.castShadow = true;
                    shadowLight.shadow.mapSize.set(shadowSpec.mapSize, shadowSpec.mapSize);
                    shadowLight.shadow.bias = shadowSpec.bias;
                    shadowLight.shadow.normalBias = shadowSpec.normalBias;
                    scene.add(shadowLight);
                    scene.add(shadowLight.target);
                }
                if (next === shadowsOn) { if (next) fitShadows(); return; }
                shadowsOn = next;
                renderer.shadowMap.enabled = shadowsOn;
                renderer.shadowMap.type = THREE[SHADOW_TYPE[shadowSpec && shadowSpec.type] || 'PCFShadowMap'];
                renderer.shadowMap.autoUpdate = false;
                if (shadowLight) shadowLight.visible = shadowsOn;
                if (shadowRoot) markShadows(shadowRoot, shadowsOn);
                fitShadows();
                recompile();
                apply();
            },
            /** Re-aim after the scene was rebuilt (new meshes, new bounds). */
            fitShadows: fitShadows,
            /** Draw the shadow map again on the next frame (the scene changed without a rebuild). */
            refreshShadows: refreshShadows,
            /** `filter(mesh) === false` keeps a mesh from casting; applied at the next fit. */
            setShadowCasterFilter: function (filter) { casterFilter = filter || null; },
            dispose: function () {
                if (shadowLight) {
                    if (shadowLight.target.parent) shadowLight.target.parent.remove(shadowLight.target);
                    if (shadowLight.parent) shadowLight.parent.remove(shadowLight);
                    shadowLight.dispose();
                }
                lights.forEach(function (light) {
                    if (light.target && light.target.parent) light.target.parent.remove(light.target);
                    if (light.parent) light.parent.remove(light);
                    if (typeof light.dispose === 'function') light.dispose();
                });
                disposeEnvironment();
            },
        };
    }

    return {
        kitLightDirection: kitLightDirection,
        configureRenderer: configureRenderer,
        roomEnvironmentScene: roomEnvironmentScene,
        disposeScene: disposeScene,
        installEnvironment: installEnvironment,
        install: install,
    };
});
