import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The UI port and the backend it proxies to both come from the environment so
// `start_server.sh --app react --port N` can place them, and both keep working
// defaults for a bare `npm run dev`. 8010/8005 is the side-by-side pair used
// while migrating panel by panel.
const UI_PORT = Number(process.env.DCDTUNI_WEB_PORT ?? 8010)
const API_TARGET = process.env.DCDTUNI_API_TARGET ?? 'http://localhost:8005'

export default defineConfig({
  plugins: [react()],
  test: {
    // jsdom, not node: the store now depends on the shared .cfdproj format
    // modules, and those are UMD files that attach to `window`. Running the
    // tests without a window would mean stubbing the very code they verify.
    environment: 'jsdom',
  },
  server: {
    port: UI_PORT,
    // strictPort: the launcher has already picked a free port and printed it;
    // silently sliding to another one would make that printed URL wrong.
    strictPort: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      // The SSE log stream must not be buffered or the mesh/solve log arrives
      // in one lump at the end instead of line by line.
      '/api/log-stream': { target: API_TARGET, changeOrigin: true, ws: false },
      // The PBR surface images live beside the legacy editor and both apps load
      // the same set. Without this rule Vite answers /textures/... with the SPA
      // index.html, and Three builds a texture whose image is a web page — so
      // the material has a map, the mesh renders flat WHITE, and it reads as
      // "textures are broken" rather than "the image was never a JPEG".
      '/textures': { target: API_TARGET, changeOrigin: true },
    },
    fs: {
      // src/frontend/shared/js holds the .cfdproj format modules (schema, the
      // v3->v4 migrator, the V4 state factory). Those are shared with the
      // legacy app on purpose — see src/legacy/cfdprojModules.ts — so the dev
      // server has to be allowed to serve from outside this package.
      allow: ['..', '../../backend/liquidcooling/data'],
    },
  },
})
