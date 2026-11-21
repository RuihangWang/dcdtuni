import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

// Reference-demo side channels share the bridge's checkout-owned defaults.
const CHECKOUT_ROOT = path.resolve(__dirname, "../../../../..");
function checkoutReadPath(key: string, filename: string): string {
  const value = process.env[key] ?? path.join(
    process.env.DC_CFD_RUNTIME_DIR ?? path.join(CHECKOUT_ROOT, ".codex-tmp/ov-bridge-metadata"), filename);
  if (!path.isAbsolute(value)) throw new Error(key + " must be absolute inside this checkout");
  const resolved = path.resolve(value);
  const relative = path.relative(CHECKOUT_ROOT, resolved);
  if (!relative || relative === ".." || relative.startsWith(".." + path.sep) || path.isAbsolute(relative)) {
    throw new Error(key + " must stay inside this checkout");
  }
  let current = CHECKOUT_ROOT;
  for (const part of relative.split(path.sep)) {
    current = path.join(current, part);
    try {
      if (fs.lstatSync(current).isSymbolicLink()) throw new Error(key + " must not use symlink components");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
  return resolved;
}
const KIT_LOG = checkoutReadPath("DC_CFD_KIT_LOG", "kit.log");
const TAIL_BYTES = 64 * 1024; // initial backlog sent on connect
const POLL_MS = 400;

// SSE endpoint that tails the Kit log and streams each new line to the browser.
// The Operation Log panel (LogPanel.tsx) consumes /api/kit-log and feeds the lines
// into the global logger as src "kit". No extra process: it runs inside the Vite
// dev server already serving the UI on :8081.
function kitLogSse(): Plugin {
  return {
    name: "dc-cfd-kit-log-sse",
    configureServer(server) {
      server.middlewares.use("/api/kit-log", (req, res) => {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
          "X-Accel-Buffering": "no",
        });
        res.write("retry: 2000\n\n");

        const send = (line: string) => {
          if (line.length) res.write(`data: ${JSON.stringify(line)}\n\n`);
        };

        let pos = 0;
        // Send the recent backlog (last TAIL_BYTES) so the panel isn't empty on open.
        try {
          const st = fs.statSync(KIT_LOG);
          const start = Math.max(0, st.size - TAIL_BYTES);
          const fd = fs.openSync(KIT_LOG, "r");
          const buf = Buffer.alloc(st.size - start);
          fs.readSync(fd, buf, 0, buf.length, start);
          fs.closeSync(fd);
          pos = st.size;
          const lines = buf.toString("utf8").split("\n");
          // drop the first (likely partial) line when we seeked into the middle
          (start > 0 ? lines.slice(1) : lines).slice(-300).forEach(send);
        } catch {
          pos = 0; // log not created yet — the poll loop will pick it up
        }

        let carry = "";
        const timer = setInterval(() => {
          let st: fs.Stats;
          try {
            st = fs.statSync(KIT_LOG);
          } catch {
            return; // file gone/not yet there
          }
          if (st.size < pos) {
            pos = 0;
            carry = "";
          } // rotated/truncated
          if (st.size <= pos) return;
          try {
            const fd = fs.openSync(KIT_LOG, "r");
            const buf = Buffer.alloc(st.size - pos);
            fs.readSync(fd, buf, 0, buf.length, pos);
            fs.closeSync(fd);
            pos = st.size;
            const text = carry + buf.toString("utf8");
            const parts = text.split("\n");
            carry = parts.pop() ?? ""; // keep the trailing partial line for next poll
            parts.forEach(send);
          } catch {
            /* transient read error; retry next tick */
          }
        }, POLL_MS);

        req.on("close", () => clearInterval(timer));
      });
    },
  };
}

// Model-tree manifest. Kit writes it after every geometry build (scenetree.py); this
// serves it back so the React tree can render what the scene ACTUALLY contains. The
// WebRTC command channel is one-way, so a file plus this endpoint is the return path —
// without it the tree was a hardcoded GTC list whose rows toggled groups a .cfdproj
// build never creates.
const SCENE_TREE = checkoutReadPath("DC_CFD_SCENE_TREE", "scene-tree.json");

function sceneTree(): Plugin {
  return {
    name: "dc-cfd-scene-tree",
    configureServer(server) {
      server.middlewares.use("/api/scene-tree", (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        try {
          const st = fs.statSync(SCENE_TREE);
          res.statusCode = 200;
          // mtime lets the page tell a manifest written by THIS build from one left
          // behind by the previous scene — the Kit process outlives any page load.
          const body = JSON.parse(fs.readFileSync(SCENE_TREE, "utf8"));
          res.end(JSON.stringify({ ...body, mtime: st.mtimeMs }));
        } catch (e) {
          res.statusCode = 404;
          res.end(JSON.stringify({ error: String(e) }));
        }
      });
    },
  };
}

// Per-dataset field ranges, written by Kit on each successful load_dataset. Lets the
// threshold sliders open on THIS result instead of the GTC/A3 constants they used to be
// pinned to. Same file-plus-endpoint side channel as the scene tree, for the same
// reason: the WebRTC command channel is one-way.
const DATASET_INFO = checkoutReadPath("DC_CFD_DATASET_INFO", "dataset-info.json");

function datasetInfo(): Plugin {
  return {
    name: "dc-cfd-dataset-info",
    configureServer(server) {
      server.middlewares.use("/api/dataset-info", (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.setHeader("Cache-Control", "no-store");
        try {
          const st = fs.statSync(DATASET_INFO);
          res.statusCode = 200;
          const body = JSON.parse(fs.readFileSync(DATASET_INFO, "utf8"));
          res.end(JSON.stringify({ ...body, mtime: st.mtimeMs }));
        } catch (e) {
          res.statusCode = 404;
          res.end(JSON.stringify({ error: String(e) }));
        }
      });
    },
  };
}

// Dataset file extensions the Kit backend can load (load_dataset + converters).
const DATASET_EXTS = [".vtu", ".vtm", ".vti", ".vts", ".vtp", ".vtk", ".case"];
// Root the file browser cannot escape above (the Kit host repo). Override with
// DC_CFD_BROWSE_ROOT. The dialog opens at DC_CFD_BROWSE_START (default: demo data).
// Derived from this config's own location so a clone anywhere works unchanged:
// web -> cfd_demo -> omniverse -> frontend -> src -> repo root.
const REPO_ROOT = process.env.DC_CFD_BROWSE_ROOT
  || path.resolve(__dirname, "../../../../..");
const BROWSE_ROOT = REPO_ROOT;

// Server-side directory browser for the "Load dataset" dialog (modeled on the
// STLGen frontend's /api/browse-dir). Returns the folders + dataset files under a
// requested path so the React file picker can navigate the Kit host filesystem.
// Read-only; refuses to list outside BROWSE_ROOT.
function browseDir(): Plugin {
  return {
    name: "dc-cfd-browse-dir",
    configureServer(server) {
      server.middlewares.use("/api/browse-dir", (req, res) => {
        const sendJson = (code: number, obj: unknown) => {
          res.statusCode = code;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify(obj));
        };
        try {
          const u = new URL(req.url || "", "http://localhost");
          // Optional ?exts=.json,.vtu filter; defaults to all dataset formats.
          const extsParam = (u.searchParams.get("exts") || "")
            .split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
          const exts = extsParam.length ? extsParam : DATASET_EXTS;
          let dir = u.searchParams.get("path") || BROWSE_ROOT;
          dir = path.resolve(dir);
          // Clamp inside BROWSE_ROOT (no traversal above the repo root).
          const root = path.resolve(BROWSE_ROOT);
          if (dir !== root && !dir.startsWith(root + path.sep)) dir = root;
          const ents = fs.readdirSync(dir, { withFileTypes: true });
          const dirs: { name: string }[] = [];
          const files: { name: string; size: number; mtime: number }[] = [];
          for (const e of ents) {
            if (e.name.startsWith(".")) continue; // hide dotfiles
            const full = path.join(dir, e.name);
            try {
              if (e.isDirectory()) {
                dirs.push({ name: e.name });
              } else if (e.isFile()) {
                const ext = path.extname(e.name).toLowerCase();
                if (exts.includes(ext)) {
                  const st = fs.statSync(full);
                  files.push({ name: e.name, size: st.size, mtime: st.mtimeMs });
                }
              }
            } catch {
              /* unreadable entry — skip */
            }
          }
          dirs.sort((a, b) => a.name.localeCompare(b.name));
          files.sort((a, b) => a.name.localeCompare(b.name));
          const parent = dir === root ? null : path.dirname(dir);
          sendJson(200, { cwd: dir, parent, root, dirs, files });
        } catch (e) {
          sendJson(400, { error: String(e) });
        }
      });
    },
  };
}

// ── AI inference worker bridge ───────────────────────────────────────────────
// The worker is a separate long-lived process (see the bundle's scripts/ai_worker.py),
// deliberately NOT inside Kit: a PyTorch forward pass on Kit's main thread would freeze
// the RTX viewport and can drop the single WebRTC client. The browser cannot reach it
// directly under the page's origin, so the dev server proxies three endpoints:
//
//   GET  /api/ai-status   -> worker /status   (is it up, which GPUs, TTA on?)
//   POST /api/ai-predict  -> worker /predict  (run one prediction; long, ~40-60 s)
//        The body is forwarded verbatim, so an optional `roi: {min:[x,y,z], max:[x,y,z]}`
//        (demo world frame, metres, Y-up) reaches the worker unchanged. It restricts what
//        the worker EXPORTS, never what the model infers.
//   GET  /api/ai-log      -> SSE tail of the worker log, same shape as /api/kit-log
//
// Override the worker location with DC_CFD_AI_PORT / DC_CFD_AI_HOST.
const AI_HOST = process.env.DC_CFD_AI_HOST || "127.0.0.1";
const AI_PORT = process.env.DC_CFD_AI_PORT || "8302";
const AI_LOG = checkoutReadPath("DC_CFD_AI_LOG", "ai-worker.log");

function aiBridge(): Plugin {
  const base = `http://${AI_HOST}:${AI_PORT}`;
  const sendJson = (res: any, code: number, obj: unknown) => {
    const body = JSON.stringify(obj);
    res.statusCode = code;
    res.setHeader("Content-Type", "application/json");
    res.end(body);
  };
  return {
    name: "dc-cfd-ai-bridge",
    configureServer(server) {
      server.middlewares.use("/api/ai-status", async (_req, res) => {
        try {
          const r = await fetch(`${base}/status`, { signal: AbortSignal.timeout(3000) });
          sendJson(res, r.status, await r.json());
        } catch (e) {
          // Offline is a normal state (DC_CFD_AI=0), not an error the UI should crash on.
          sendJson(res, 200, { ok: false, offline: true, error: String(e) });
        }
      });

      server.middlewares.use("/api/ai-predict", async (req, res) => {
        if (req.method !== "POST") return sendJson(res, 405, { error: "POST only" });
        const chunks: Buffer[] = [];
        req.on("data", (c) => chunks.push(c));
        req.on("end", async () => {
          try {
            // No timeout: a cold multi-rank run can take minutes, and aborting here
            // would leave the worker running with nobody reading the result.
            const r = await fetch(`${base}/predict`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: Buffer.concat(chunks).toString("utf8") || "{}",
            });
            sendJson(res, r.status, await r.json());
          } catch (e) {
            sendJson(res, 502, { ok: false, error: `AI worker unreachable: ${e}` });
          }
        });
      });

      // Same tail-and-stream shape as /api/kit-log so LogPanel can consume both.
      server.middlewares.use("/api/ai-log", (req, res) => {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
          "X-Accel-Buffering": "no",
        });
        res.write("retry: 2000\n\n");
        const send = (line: string) => {
          if (line.length) res.write(`data: ${JSON.stringify(line)}\n\n`);
        };
        let pos = 0;
        try {
          const st = fs.statSync(AI_LOG);
          const start = Math.max(0, st.size - TAIL_BYTES);
          const fd = fs.openSync(AI_LOG, "r");
          const buf = Buffer.alloc(st.size - start);
          fs.readSync(fd, buf, 0, buf.length, start);
          fs.closeSync(fd);
          pos = st.size;
          const lines = buf.toString("utf8").split("\n");
          (start > 0 ? lines.slice(1) : lines).slice(-200).forEach(send);
        } catch {
          pos = 0;
        }
        let carry = "";
        const timer = setInterval(() => {
          let st: fs.Stats;
          try {
            st = fs.statSync(AI_LOG);
          } catch {
            return;
          }
          if (st.size < pos) {
            pos = 0;
            carry = "";
          }
          if (st.size <= pos) return;
          try {
            const fd = fs.openSync(AI_LOG, "r");
            const buf = Buffer.alloc(st.size - pos);
            fs.readSync(fd, buf, 0, buf.length, pos);
            fs.closeSync(fd);
            pos = st.size;
            const parts = (carry + buf.toString("utf8")).split("\n");
            carry = parts.pop() ?? "";
            parts.forEach(send);
          } catch {
            /* transient */
          }
        }, POLL_MS);
        req.on("close", () => clearInterval(timer));
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), kitLogSse(), browseDir(), aiBridge(), sceneTree(),
            datasetInfo()],
  server: { host: "0.0.0.0", port: 8081, cors: true },
  // The browser cannot read the filesystem, but the paths it sends to Kit are
  // SERVER-side paths — so the repo root has to come from the build. Injecting it
  // here is what lets geometry.ts stop hardcoding one machine's checkout.
  define: { __DC_REPO_ROOT__: JSON.stringify(REPO_ROOT) },
});
