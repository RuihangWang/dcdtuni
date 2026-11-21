/**
 * Eye-Dome Lighting — the post-processing tab's "深度增强 (AO)".
 *
 * A ParaView-style depth cue: render the scene to a colour+depth target, then a
 * fullscreen pass darkens pixels that sit behind their neighbours. On a room
 * full of same-coloured racks this is what separates one from the next; without
 * it the geometry reads as a single silhouette.
 *
 * Ported from `_ppAOEnsure` / `_ppAORender` (STL_generate.html:29790) with the
 * shader kept verbatim, including the two things that are easy to get wrong:
 *
 * - **The background is skipped.** At the far plane the depth difference to
 *   every neighbour is enormous, so the pass would paint a black frame around
 *   the model.
 * - **The pass encodes sRGB itself.** The render target holds LINEAR
 *   tone-mapped colour and a custom ShaderMaterial gets no automatic
 *   conversion, so without the encode the whole image comes back gamma-dark —
 *   which looks like the AO is far too strong rather than like a colour-space
 *   bug.
 *
 * Off by default. When off, nothing here is constructed and the viewport
 * renders straight to the screen exactly as before.
 */

import * as THREE from 'three'

export interface EdlPass {
  /** Render scene/camera to the screen with the depth darkening applied. */
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera): void
  setStrength(v: number): void
  setRadius(v: number): void
  strength(): number
  dispose(): void
}

const VERT = `varying vec2 vUv;
void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }`

const FRAG = `uniform sampler2D tDiffuse;
uniform sampler2D tDepth;
uniform vec2 uResolution;
uniform float uStrength;
uniform float uRadius;
uniform float uCameraNear;
uniform float uCameraFar;
varying vec2 vUv;
float linDepth(vec2 uv){
    float d = texture2D(tDepth, uv).x;
    float z = d * 2.0 - 1.0;
    return (2.0 * uCameraNear * uCameraFar) / (uCameraFar + uCameraNear - z * (uCameraFar - uCameraNear));
}
void main(){
    vec4 color = texture2D(tDiffuse, vUv);
    float d0 = linDepth(vUv);
    if (d0 >= uCameraFar * 0.99) { gl_FragColor = color; return; }
    vec2 px = uRadius / uResolution;
    float resp = 0.0;
    vec2 dirs[8];
    dirs[0]=vec2(1.,0.); dirs[1]=vec2(-1.,0.); dirs[2]=vec2(0.,1.); dirs[3]=vec2(0.,-1.);
    dirs[4]=vec2(.707,.707); dirs[5]=vec2(-.707,.707); dirs[6]=vec2(.707,-.707); dirs[7]=vec2(-.707,-.707);
    for (int i=0;i<8;i++){
        float dn = linDepth(vUv + dirs[i]*px);
        resp += max(0.0, log2(d0) - log2(dn));
    }
    resp /= 8.0;
    float shade = exp(-uStrength * resp * 40.0);
    vec3 lin = color.rgb * clamp(shade, 0.0, 1.0);
    vec3 srgb = mix(lin * 12.92, 1.055 * pow(max(lin, vec3(0.0)), vec3(1.0/2.4)) - 0.055, step(0.0031308, lin));
    gl_FragColor = vec4(srgb, color.a);
}`

export function createEdlPass(renderer: THREE.WebGLRenderer): EdlPass {
  const material = new THREE.ShaderMaterial({
    uniforms: {
      tDiffuse: { value: null },
      tDepth: { value: null },
      uResolution: { value: new THREE.Vector2(1, 1) },
      uStrength: { value: 2.0 },
      uRadius: { value: 2.0 },
      uCameraNear: { value: 0.1 },
      uCameraFar: { value: 1000.0 },
    },
    vertexShader: VERT,
    fragmentShader: FRAG,
    depthTest: false,
    depthWrite: false,
  })
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material)
  quad.frustumCulled = false
  const scene = new THREE.Scene()
  scene.add(quad)
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
  let target: THREE.WebGLRenderTarget | null = null

  function ensureTarget(w: number, h: number) {
    if (target && target.width === w && target.height === h) return
    if (target) {
      target.setSize(w, h)
      return
    }
    const depth = new THREE.DepthTexture(w, h)
    // 24-bit integer depth. The 16-bit default quantises a 40 m room into steps
    // coarse enough that flat walls develop banded occlusion.
    depth.type = THREE.UnsignedIntType
    target = new THREE.WebGLRenderTarget(w, h, { depthTexture: depth })
  }

  return {
    render(sceneToDraw, cam) {
      const size = renderer.getDrawingBufferSize(new THREE.Vector2())
      const w = Math.max(1, Math.floor(size.x))
      const h = Math.max(1, Math.floor(size.y))
      ensureTarget(w, h)
      renderer.setRenderTarget(target)
      renderer.clear()
      renderer.render(sceneToDraw, cam)
      renderer.setRenderTarget(null)
      material.uniforms.tDiffuse!.value = target!.texture
      material.uniforms.tDepth!.value = target!.depthTexture
      ;(material.uniforms.uResolution!.value as THREE.Vector2).set(w, h)
      material.uniforms.uCameraNear!.value = cam.near
      material.uniforms.uCameraFar!.value = cam.far
      renderer.render(scene, camera)
    },
    setStrength(v) {
      material.uniforms.uStrength!.value = v
    },
    setRadius(v) {
      material.uniforms.uRadius!.value = v
    },
    strength() {
      return material.uniforms.uStrength!.value as number
    },
    dispose() {
      quad.geometry.dispose()
      material.dispose()
      target?.dispose()
      target = null
    },
  }
}
