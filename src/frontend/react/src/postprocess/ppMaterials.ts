/**
 * The post-processing shaders.
 *
 * All three are ports of the legacy editor's materials, kept verbatim because
 * the point of them is that the two apps produce the SAME picture from the same
 * result. Two things they deliberately are not:
 *
 * - **Not MeshStandardMaterial.** A physically-based material puts the scalar
 *   colours through the renderer's ACES tone mapping and its lighting model, so
 *   the surface no longer shows the colour the legend says it does — everything
 *   drifts warm. A raw ShaderMaterial writes its fragment colour out untouched.
 *
 * - **Not baked vertex colours.** The geometry carries the raw scalar in a
 *   `fieldValue` attribute and the LUT is sampled per fragment. Interpolating
 *   RGB across a triangle passes through hues in neither end colour, which is
 *   the "三角离散" banding the legacy code comments call out.
 */

import * as THREE from 'three'

/** Raw range equality is a display decision, before GPU float rounding. The
 * getter follows existing imperative min/max updates without extra UI state. */
export function scalarRangeUniforms(minValue: number, maxValue: number) {
  const uMinValue = { value: minValue }, uMaxValue = { value: maxValue }
  return { uMinValue, uMaxValue, uFlatRange: { get value() { return uMinValue.value === uMaxValue.value } } }
}

/**
 * The lit surface material — boundary patches and streamline tubes.
 *
 * ParaView-style shading: pure diffuse, no specular, and the VTK LightKit
 * directions in view space, so the shading follows the camera the way the
 * legend expects. The raw LightKit sum peaks near 1.68, which blew lit faces
 * toward white while faces turned away went black; both destroyed the match
 * with the colorbar, so the diffuse term is clamped and an ambient floor keeps
 * shaded faces readable.
 */
export function createFieldMaterial(
  colormap: THREE.Texture,
  minValue: number,
  maxValue: number,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      ...scalarRangeUniforms(minValue, maxValue),
      uColorMap: { value: colormap },
      uOpacity: { value: 1.0 },
      uLightIntensity: { value: 1.0 },
    },
    vertexShader: `attribute float fieldValue;
varying float vFieldValue;
varying vec3 vNormal;
void main() {
    vFieldValue = fieldValue;
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`,
    fragmentShader: `uniform float uMinValue;
uniform float uMaxValue;
uniform bool uFlatRange;
uniform sampler2D uColorMap;
uniform float uOpacity;
uniform float uLightIntensity;
varying float vFieldValue;
varying vec3 vNormal;
void main() {
    // A collapsed display LUT maps to its first entry, even when clipping
    // leaves original field samples outside the zero-width domain.
    float t = uFlatRange ? 0.0 : clamp((vFieldValue - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);
    vec3 diffuseColor = texture2D(uColorMap, vec2(t, 0.5)).rgb;

    vec3 n = normalize(vNormal);
    if (!gl_FrontFacing) n = -n;

    float diff = 0.0;
    diff += 0.75   * max(dot(n, normalize(vec3( 0.1116,  0.7660,  0.6330))), 0.0);
    diff += 0.25   * max(dot(n, normalize(vec3(-0.0449, -0.9659,  0.2549))), 0.0);
    diff += 0.25   * max(dot(n, vec3(0.0, 0.0, 1.0)), 0.0);
    diff += 0.2143 * max(dot(n, normalize(vec3( 0.9397,  0.0, -0.3420))), 0.0);
    diff += 0.2143 * max(dot(n, normalize(vec3(-0.9397,  0.0, -0.3420))), 0.0);

    float ambient = 0.40;
    float shade = ambient + (1.0 - ambient) * clamp(diff, 0.0, 1.0);
    if (uOpacity < 0.01) discard;
    vec3 litColor = diffuseColor * shade * uLightIntensity;

    gl_FragColor = vec4(litColor, uOpacity);
}`,
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: true,
    // Pushes the result surface toward the camera so it wins the depth test
    // against coplanar model geometry — without it the floor grid bleeds
    // through the floor patch wherever the two z-fight.
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  })
}

/**
 * The unlit material — slices.
 *
 * A slice is a flat cut through the volume; lighting it would shade one plane
 * unevenly and make the same temperature read as two different colours across
 * the cut. Unlit keeps every fragment at exactly its LUT colour.
 */
export function createScalarFlatMaterial(
  colormap: THREE.Texture,
  minValue: number,
  maxValue: number,
  opacity = 0.95,
): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: {
      ...scalarRangeUniforms(minValue, maxValue),
      uColorMap: { value: colormap },
      uOpacity: { value: opacity },
    },
    vertexShader: `attribute float fieldValue;
varying float vFieldValue;
void main() {
    vFieldValue = fieldValue;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`,
    fragmentShader: `uniform float uMinValue;
uniform float uMaxValue;
uniform bool uFlatRange;
uniform sampler2D uColorMap;
uniform float uOpacity;
varying float vFieldValue;
void main() {
    // A collapsed display LUT maps to its first entry, even when clipping
    // leaves original field samples outside the zero-width domain.
    float t = uFlatRange ? 0.0 : clamp((vFieldValue - uMinValue) / (uMaxValue - uMinValue + 1e-10), 0.0, 1.0);
    if (uOpacity < 0.01) discard;
    gl_FragColor = vec4(texture2D(uColorMap, vec2(t, 0.5)).rgb, uOpacity);
}`,
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
  })
}
