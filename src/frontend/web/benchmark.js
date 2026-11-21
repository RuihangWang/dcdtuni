(() => {
  const t = window.__three;
  if (!t || !t.scene || !t.renderer || !t.camera || !t.globalGroup) {
    return JSON.stringify({ error: 'window.__three not available' });
  }

  const results = {};

  // 1. Mesh count — traverse globalGroup
  let meshCount = 0;
  let lineCount = 0;
  let totalTriangles = 0;
  let totalVertices = 0;
  t.globalGroup.traverse(obj => {
    if (obj.isMesh) {
      meshCount++;
      if (obj.geometry) {
        const idx = obj.geometry.index;
        totalTriangles += idx ? idx.count / 3 : (obj.geometry.attributes.position ? obj.geometry.attributes.position.count / 3 : 0);
        if (obj.geometry.attributes.position) totalVertices += obj.geometry.attributes.position.count;
      }
    }
    if (obj.isLine || obj.isLineSegments || obj.isLineLoop) lineCount++;
  });
  results.meshCount = meshCount;
  results.lineCount = lineCount;
  results.totalTriangles = Math.round(totalTriangles);
  results.totalVertices = totalVertices;

  // 2. buildGeometryData() timing
  if (typeof window.buildGeometryData === 'function') {
    const bgdStart = performance.now();
    window.buildGeometryData();
    const bgdEnd = performance.now();
    results.buildGeometryDataMs = Math.round((bgdEnd - bgdStart) * 100) / 100;
  } else {
    results.buildGeometryDataMs = null;
    results.buildGeometryDataError = 'function not found';
  }

  // 3. Average render frame time (10 frames)
  {
    const renderer = t.renderer;
    const scene = t.scene;
    const camera = t.camera;
    const times = [];
    for (let i = 0; i < 10; i++) {
      const s = performance.now();
      renderer.render(scene, camera);
      const e = performance.now();
      times.push(e - s);
    }
    const avg = times.reduce((a, b) => a + b, 0) / times.length;
    results.renderFrameAvgMs = Math.round(avg * 100) / 100;
    results.renderFrameMinMs = Math.round(Math.min(...times) * 100) / 100;
    results.renderFrameMaxMs = Math.round(Math.max(...times) * 100) / 100;
    results.renderFrameFPS = Math.round(1000 / avg * 10) / 10;
  }

  // 4. renderScene() timing (includes updateHighlights if available)
  // renderScene is closure-scoped, not on window. We measure it indirectly
  // by timing the full _doRender path or just skip if not accessible.
  // Since updateHighlights is called inside renderScene() which is closure-scoped,
  // we cannot directly measure it. Instead, measure the overhead of a full
  // renderer.render() which is the main bottleneck anyway.
  results.updateHighlightsNote = 'closure-scoped; not directly measurable from window';

  // 5. 30-frame camera rotation benchmark
  {
    const renderer = t.renderer;
    const scene = t.scene;
    const camera = t.camera;

    // Save original camera state
    const origPos = camera.position.clone();
    const origTarget = t.controls ? t.controls.target.clone() : null;

    // Compute rotation center and radius
    const center = origTarget || new THREE.Vector3(0, 0, 0);
    const offset = camera.position.clone().sub(center);
    const radius = offset.length();
    const baseAngle = Math.atan2(offset.x, offset.z);
    const elevation = Math.asin(offset.y / radius);

    const frameCount = 30;
    const frameTimes = [];

    for (let i = 0; i < frameCount; i++) {
      const angle = baseAngle + (2 * Math.PI * i) / frameCount;
      camera.position.set(
        center.x + radius * Math.sin(angle) * Math.cos(elevation),
        center.y + radius * Math.sin(elevation),
        center.z + radius * Math.cos(angle) * Math.cos(elevation)
      );
      camera.lookAt(center);
      camera.updateMatrixWorld(true);

      const s = performance.now();
      renderer.render(scene, camera);
      const e = performance.now();
      frameTimes.push(e - s);
    }

    // Restore camera
    camera.position.copy(origPos);
    if (origTarget && t.controls) {
      t.controls.target.copy(origTarget);
    }
    camera.lookAt(origTarget || new THREE.Vector3(0, 0, 0));
    camera.updateMatrixWorld(true);
    renderer.render(scene, camera);

    const avg = frameTimes.reduce((a, b) => a + b, 0) / frameTimes.length;
    results.rotationBenchmark = {
      frames: frameCount,
      avgFrameMs: Math.round(avg * 100) / 100,
      minFrameMs: Math.round(Math.min(...frameTimes) * 100) / 100,
      maxFrameMs: Math.round(Math.max(...frameTimes) * 100) / 100,
      avgFPS: Math.round(1000 / avg * 10) / 10,
      totalMs: Math.round(frameTimes.reduce((a, b) => a + b, 0) * 100) / 100
    };
  }

  // Metadata
  results.timestamp = new Date().toISOString();
  results.modelCount = typeof state !== 'undefined' ? state.construction.length : null;
  results.rendererInfo = t.renderer.info ? {
    memory: {
      geometries: t.renderer.info.memory.geometries,
      textures: t.renderer.info.memory.textures
    },
    render: {
      calls: t.renderer.info.render.calls,
      triangles: t.renderer.info.render.triangles,
      points: t.renderer.info.render.points,
      lines: t.renderer.info.render.lines
    }
  } : null;

  return JSON.stringify(results, null, 2);
})()
