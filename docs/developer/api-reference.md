# REST API Reference

Documentation for the REST endpoints of `src/server/server.py`, checked against its routing table as of 2026-10-21. Static assets (`/js/*`, `/vendor/*`, `/textures/*`, `/i18n/*`, …) and their authenticated `/api/` aliases (`/api/textures/`, `/api/vendor/`, `/api/resources/`, `/api/css/`, `/api/theme/`, `/api/js/`, `/api/favicon.ico`) are served by the same handler but are not REST endpoints. A summary table of every route is at the end.

**Base URL:** the port the launcher prints, `http://localhost:8000` by default (`server.py` run directly defaults to 8005). The examples below use `$PORT`.

**Authentication:** cookie-based sessions. `POST /api/auth/login` sets the cookie `stlgen_session_<port>` (named per HTTP port, so two servers on one host do not share sessions; the old unscoped `stlgen_session` is still accepted for this server's own sessions). Every `/api/*` route except `/api/auth/login` and `/api/auth/check` requires a session and answers `401 {"error": "Authentication required"}` without one. Loopback clients are not trusted unless the server was started with `--trust-loopback`. The server's own headless browser uses a random worker session.

There is no built-in password: the administrator account comes from `DCDTUNI_ADMIN_USER` / `DCDTUNI_ADMIN_PASSWORD`, from `--admin-credentials <0600 file>`, or, in the launcher's local mode, from `src/server/auth-local/admin_credentials` (see [Installation](../user-guide/installation.md#logging-in)).

**Server modes:** `--app react` (default: the React build and `/api`), `--app html` (the legacy page and `/api`) and `--app api` (`/api` only: non-`/api` paths return 404 and `/api/browser/*` returns 409 except `/api/browser/close`). The scripted API runs its private servers in `api` mode.

**Roles:**
- **admin** — full access to all endpoints
- **viewer** — every GET route except `/api/admin/*`; POST limited to `/api/pp/init`, `/api/pp/render-grant`, `/api/mesh-preview/render-grant` and `/api/auth/logout`

**Error Format:**
Errors return JSON, usually `{"error": "description"}` (some routes add `code` or `ok: false`), with HTTP status codes 400, 401, 403, 404, 408, 409, 428, 500 or 501.

**CORS:** JSON responses include `Access-Control-Allow-Origin: *`.

**Result revision:** the post-processing data routes require the `X-PP-Revision` header returned by `POST /api/pp/init` (missing: 428, stale: 409); see §6.

---

## 1. Authentication

### `POST /api/auth/login`

**Auth:** Public (no session required)

Validate username and password. On success, sets the `stlgen_session_<port>` HttpOnly cookie and returns the session token. The token can also be passed as a WebSocket query parameter (`?token=...`) for the terminal connection.

IP-based lockout is enforced: 5 failures → 5 min ban, 10 → 1 h, 15 → 1 day, 20 → permanent ban.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Account username |
| `password` | string | Yes | Account password |

**Response (200):**

```json
{
  "ok": true,
  "token": "a1b2c3d4e5f6...",
  "username": "admin",
  "role": "admin"
}
```

**Response (401):**

```json
{ "ok": false, "error": "Invalid username or password" }
```

**Response (403 — IP banned):**

```json
{ "ok": false, "error": "IP banned until 2026-05-01T12:00:00" }
```

**Set-Cookie header (on success):**
```
Set-Cookie: stlgen_session_<port>=<token>; HttpOnly; Max-Age=43200; Path=/; SameSite=Lax
```
The cookie lasts 12 hours; the session also ends on logout or admin revocation.

**Example** (local-mode credentials; with the environment pair, put those values instead):

```bash
CRED=src/server/auth-local/admin_credentials
curl -c cookie.txt -X POST http://localhost:$PORT/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$(sed -n 1p $CRED)\", \"password\": \"$(sed -n 2p $CRED)\"}"
```

---

### `GET /api/auth/check`

**Auth:** Public (reads session cookie if present)

Check whether the current session is valid. The frontend polls this on page load to decide whether to show the login overlay. Only when the server was started with `--trust-loopback` do loopback clients (`127.0.0.1` / `::1`) without a session receive `authenticated: true` with `role: admin`.

**Response (200 — authenticated):**

```json
{
  "authenticated": true,
  "authRequired": true,
  "username": "admin",
  "role": "admin"
}
```

**Response (200 — not authenticated):**

```json
{
  "authenticated": false,
  "authRequired": true
}
```

Authentication cannot be disabled; a server started without credentials refuses to start.

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/auth/check
```

---

### `POST /api/auth/logout`

**Auth:** Any authenticated user (viewer or admin)

Invalidate the current session and clear the `stlgen_session_<port>` cookie (and this server's own legacy `stlgen_session`, if present). Returns `{"ok": true}` regardless of whether a valid session was present.

**Request:** Empty body (no fields required)

**Response (200):**

```json
{ "ok": true }
```

**Set-Cookie (clears session):**
```
Set-Cookie: stlgen_session_<port>=; Path=/; Max-Age=0
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/auth/logout
```

---

## 2. Admin Management

All `/api/admin/*` endpoints require **admin** role. They can be invoked via `GET`, `POST`, or `DELETE` (the server also accepts `POST` as a `DELETE` alias for clients that cannot send `DELETE`).

### `GET /api/admin/users`

**Auth:** Admin

List all registered user accounts.

**Response (200):**

```json
{
  "users": [
    { "username": "admin", "role": "admin" },
    { "username": "viewer1", "role": "viewer" }
  ]
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/admin/users
```

---

### `POST /api/admin/users`

**Auth:** Admin

Create a new user account.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | New username (alphanumeric + `_`) |
| `password` | string | Yes | Initial password |
| `role` | string | No | `"admin"` or `"viewer"` (default: `"viewer"`) |

**Response (201):**

```json
{ "ok": true, "message": "User 'alice' created" }
```

**Response (400):**

```json
{ "ok": false, "error": "username and password are required" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/admin/users \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "S3cr3t!", "role": "viewer"}'
```

---

### `DELETE /api/admin/users/<username>`  (also `POST`)

**Auth:** Admin

Delete a user account. A user cannot delete their own account. The last admin account cannot be deleted.

**URL parameter:** `username` — alphanumeric + underscore only

**Response (200):**

```json
{ "ok": true, "message": "User 'alice' deleted" }
```

**Response (400):**

```json
{ "ok": false, "error": "Cannot delete the only admin account" }
```

**Example:**

```bash
curl -b cookie.txt -X DELETE http://localhost:$PORT/api/admin/users/alice
```

---

### `POST /api/admin/users/<username>/password`

**Auth:** Admin

Change the password of an existing account.

**URL parameter:** `username` — account to update

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `password` | string | Yes | New password (stored as PBKDF2-SHA256 hash, 100 k iterations) |

**Response (200):**

```json
{ "ok": true, "message": "Password updated for 'alice'" }
```

**Example:**

```bash
curl -b cookie.txt -X POST \
  http://localhost:$PORT/api/admin/users/alice/password \
  -H "Content-Type: application/json" \
  -d '{"password": "NewP@ss123"}'
```

---

### `POST /api/admin/users/<username>/role`

**Auth:** Admin

Change the role of an existing account. An admin cannot demote themselves.

**URL parameter:** `username` — account to update

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | `"admin"` or `"viewer"` |

**Response (200):**

```json
{ "ok": true, "message": "Role for 'alice' changed to admin" }
```

**Example:**

```bash
curl -b cookie.txt -X POST \
  http://localhost:$PORT/api/admin/users/alice/role \
  -H "Content-Type: application/json" \
  -d '{"role": "admin"}'
```

---

### `GET /api/admin/sessions`

**Auth:** Admin

List all active sessions (token prefix, username, role, created time, last-seen IP).

**Response (200):**

```json
{
  "sessions": [
    {
      "tokenPrefix": "a1b2c3d4",
      "username": "admin",
      "role": "admin",
      "createdAt": "2026-04-23T10:00:00",
      "ip": "192.168.1.10"
    }
  ]
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/admin/sessions
```

---

### `DELETE /api/admin/sessions/<prefix>`  (also `POST`)

**Auth:** Admin

Revoke a session by its token prefix (the first 8 hex characters from the sessions list). This immediately logs out that user.

**URL parameter:** `prefix` — 8-character hex token prefix

**Response (200):**

```json
{ "ok": true, "message": "Session revoked" }
```

**Response (404):**

```json
{ "ok": false, "error": "Session not found" }
```

**Example:**

```bash
curl -b cookie.txt -X DELETE http://localhost:$PORT/api/admin/sessions/a1b2c3d4
```

---

### `GET /api/admin/bans`

**Auth:** Admin

List all IP bans with ban reason and expiry time.

**Response (200):**

```json
{
  "bans": [
    {
      "ip": "192.168.1.99",
      "reason": "15 failed login attempts",
      "expiresAt": "2026-04-24T12:00:00",
      "permanent": false
    }
  ]
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/admin/bans
```

---

### `DELETE /api/admin/bans/<ip>`  (also `POST`)

**Auth:** Admin

Remove an IP ban, immediately allowing that IP to attempt login again.

**URL parameter:** `ip` — IP address to unban (e.g. `192.168.1.99`)

**Response (200):**

```json
{ "ok": true, "message": "IP unbanned" }
```

**Response (404):**

```json
{ "ok": false, "error": "IP not found in bans" }
```

**Example:**

```bash
curl -b cookie.txt -X DELETE \
  "http://localhost:$PORT/api/admin/bans/192.168.1.99"
```

---

### `POST /api/admin/upgrade`

**Auth:** Admin

Pull the latest code from the upstream git remote (`git fetch` + `git pull --recurse-submodules`). If the working tree is already up to date, returns `status: already_up_to_date`. If there are local conflicts, returns `status: conflict` with the conflicting filenames; use `/api/admin/upgrade/force` to stash and retry.

!!! note "Current limits"
    The upgrade only pulls; it does not rebuild the React editor, so the old `dist/` keeps being served until the server is started again with `./src/start_server.sh --build`. The code still contains a branch for `src/frontend` as a Git submodule, which it no longer is. Next step: rebuild after a successful pull and drop the submodule branch.

**Request:** Empty body

**Response (200 — updated):**

```json
{
  "ok": true,
  "status": "updated",
  "restart": true,
  "log": ["$ git fetch origin", "$ git pull --recurse-submodules", "Pull succeeded."]
}
```

**Response (200 — up to date):**

```json
{
  "ok": true,
  "status": "already_up_to_date",
  "log": ["Already up to date."]
}
```

**Response (200 — conflict):**

```json
{
  "ok": false,
  "status": "conflict",
  "files": ["src/frontend/web/STL_generate.html"],
  "log": ["Pull failed — checking for conflicts..."]
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/admin/upgrade
```

---

### `POST /api/admin/upgrade/force`

**Auth:** Admin

Stash local changes, then pull. The stash is named `upgrade-YYYY-MM-DD-HH-MM-SS`. The stash is **not** automatically popped — retrieve it with `git stash pop` if needed.

**Request:** Empty body

**Response (200):**

```json
{
  "ok": true,
  "status": "updated",
  "stashed": true,
  "restart": true,
  "log": ["$ git stash push -m 'upgrade-2026-04-23-10-30-00'", "Pull succeeded after stash."]
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/admin/upgrade/force
```

---

### `POST /api/admin/restart`

**Auth:** Admin

Restart the server process. The current process sends a 200 response, then spawns a new `server.py` process (with the same CLI arguments) after a 2-second delay, then exits.

!!! note "Current limits"
    The new process is started outside `src/start_server.sh`. When the server was started by the launcher, the launcher sees its backend exit and runs its own clean-up, which also stops anything it started alongside (for example the Omniverse bridge). Prefer stopping with Ctrl+C or `fuser -k <port>/tcp` and starting the launcher again; next step: let the launcher own restarts.

**Request:** Empty body

**Response (200):**

```json
{ "ok": true, "message": "Restarting..." }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/admin/restart
```

---

## 3. Case & File Management

### `POST /api/set-case-dir`

**Auth:** Admin

Set (or change) the active OpenFOAM case directory. The server stores this path in memory and uses it for all subsequent mesh, solve, and post-processing operations; changing it invalidates the loaded post-processing result. The case directory is **process-wide**: one server holds one case, and the last caller wins, so run one server per user or task.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `caseDir` | string | Yes | Case directory: absolute, or relative to the repository root (e.g. `case/Base_leakage`, the form projects store) |
| `createIfMissing` | boolean | No | Default `true` (create the directory). `false` refuses a missing path with `404 case_dir_missing` — used when restoring a project's saved case path |

**Response (200):**

```json
{ "ok": true, "caseDir": "/abs/path/to/case", "portablePath": "case/MyRoom" }
```

`portablePath` is the repository-relative form when the directory lies inside the repository. Errors carry a `code`: `case_dir_missing`, `case_dir_not_directory`, `case_dir_permission_denied`, `case_dir_unavailable`.

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/set-case-dir \
  -H "Content-Type: application/json" \
  -d '{"caseDir": "/home/user/mycode/dcdtuni/case/MyRoom"}'
```

---

### `POST /api/write-file`

**Auth:** Admin

Write a text file to the server filesystem. Parent directories are created automatically. By default, writing to an existing file fails with `409 Conflict`; set `overwrite: true` to allow replacement.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Absolute path of the file to write |
| `content` | string or object | Yes | Text content (objects are JSON-serialized) |
| `overwrite` | boolean | No | Allow overwriting existing files (default: `false`) |

**Response (200):**

```json
{ "ok": true, "path": "/path/to/file.txt", "overwrote": false }
```

**Response (409 — file exists, overwrite not set):**

```json
{
  "ok": false,
  "exists": true,
  "path": "/path/to/file.txt",
  "error": "File already exists: /path/to/file.txt"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/write-file \
  -H "Content-Type: application/json" \
  -d '{"path": "/tmp/test.txt", "content": "hello world", "overwrite": true}'
```

---

### `POST /api/create-dir`

**Auth:** Admin

Create a directory (and any missing parent directories) on the server. Used by the browser file-picker dialog's "New Folder" button.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Absolute path of the directory to create |

**Response (200):**

```json
{ "ok": true, "path": "/path/to/new/dir" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/create-dir \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/mycode/dcdtuni/case/NewRoom"}'
```

---

### `GET /api/browse-dir`

**Auth:** Admin or Viewer

Browse the server filesystem for the file-picker dialog. Returns subdirectories and files (with optional extension filter) in the requested directory.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | No | Directory to list (default: home directory) |
| `extensions` | string | No | Comma-separated extension filter, e.g. `.cfdproj,.json` |

**Response (200):**

```json
{
  "current": "/home/user",
  "parent": "/home",
  "dirs": [
    { "name": "mycode", "path": "/home/user/mycode", "hidden": false, "mtime": 1745000000.0 }
  ],
  "files": [
    { "name": "project.cfdproj", "path": "/home/user/project.cfdproj", "hidden": false, "mtime": 1745000000.0, "size": 204800 }
  ]
}
```

**Example:**

```bash
curl -b cookie.txt \
  "http://localhost:$PORT/api/browse-dir?path=/home/user&extensions=.cfdproj"
```

---

### `GET /api/read-file`

**Auth:** Admin or Viewer

Read a text file from the server filesystem and return its contents. Used by the browser to load remote `.cfdproj` project files without the user having to manually upload them.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Absolute path to the file |

**Response (200):**

```json
{ "ok": true, "content": "{\"version\": 1, \"models\": [...]}" }
```

**Response (404):**

```json
{ "ok": false, "error": "File not found: /path/to/file" }
```

**Example:**

```bash
curl -b cookie.txt \
  "http://localhost:$PORT/api/read-file?path=/home/user/project.cfdproj"
```

---

### `GET /api/read-file-binary`

**Auth:** Admin or Viewer

`?path=<file>` → the file as base64 plus its MIME type, for images the editor turns into textures (`/api/read-file` is UTF-8 text only).

---

## 4. Mesh & Solve

### `POST /api/run-mesh`

**Auth:** Admin

Submit a mesh generation and/or solve job. Saves STL geometry files and a `.cfdrun` manifest to the case directory, then launches `python -m backend.aircooling.runner.run_cfd --from-cfdrun <manifest>` as a background subprocess. Returns a `jobId` immediately; use `/api/log-stream` or `/api/job-status` to monitor progress.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cfdrunJson` | string | No | JSON string of the `.cfdrun` manifest (the editor's export; the old body key `cfdExportJson` is still accepted with a deprecation warning) |
| `cfdrunFilename` | string | No | Filename for the manifest (default: `project.cfdrun`) |
| `stlFiles` | object | No | Map of `{ "filename.stl": "<base64>" }` STL files to write |
| `meshOnly` | boolean | No | Run mesh generation only, skip solve (default: `false`) |
| `solveOnly` | boolean | No | Run solve only on existing mesh (default: `false`) |
| `hybridHeat` | object | No | Air–liquid handoff: `{project, liquidRunId, sampleIndex}`. The server audits that completed liquid run and compiles its heat into a derived air `.cfdrun` before solving (see [Boundary Conditions](bridge-bc.md#liquid-cooling-integration)) |
| `meshPreparationElapsedSeconds` | number | No | Time the client already spent preparing the export; counted against the mesh time target |

If `cfdrunJson` is omitted, the server uses the existing `.cfdrun` file in `case/model/`.
The server refuses a second job on a case that is already running one (`409 case_busy`). Mesh jobs end with a strict `checkMesh` receipt; solve jobs refuse a mesh without an accepted receipt.
If `stlFiles` is provided, all existing `.stl` and `.eMesh` files in `case/Mesh/constant/triSurface/` are replaced before the job starts.

**Response (200):**

```json
{ "jobId": "a1b2c3d4" }
```

**Response (400):**

```json
{ "error": "No case directory set. Use --case-dir on startup or POST /api/set-case-dir first." }
```

**Response (500):**

```json
{ "error": "dcdtuni not available: <import error or version>" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/run-mesh \
  -H "Content-Type: application/json" \
  -d '{"meshOnly": true}'
```

---

### `POST /api/run-check-mesh`

**Auth:** Admin

Run the OpenFOAM `checkMesh` utility on the current mesh (`case/Mesh/`) as a background job. Returns a `jobId`; stream output with `/api/log-stream`. When the job completes, `/api/job-status` includes `checkMeshOk` and `meshWarnings`.

**Request:** Empty body

**Response (200):**

```json
{ "jobId": "b2c3d4e5" }
```

**Response (404 — no mesh):**

```json
{ "error": "No mesh found. Run mesh generation first." }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/run-check-mesh
```

---

### `GET /api/check-mesh`

**Auth:** Admin or Viewer

Check whether a polyMesh exists at `case/Mesh/constant/polyMesh/points`. Non-blocking — returns immediately.

**Response (200 — mesh exists):**

```json
{
  "meshReady": true,
  "polyMeshPath": "/path/to/case/Mesh/constant/polyMesh"
}
```

**Response (200 — no mesh):**

```json
{ "meshReady": false, "reason": "No case directory set" }
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/check-mesh
```

---

### `POST /api/prepare-case`

**Auth:** Admin

Save STL files and the `.cfdrun` manifest to disk (same as `/api/run-mesh`), but **do not** launch the subprocess. Instead, return the shell command string that the user can run manually or paste into the WebSocket terminal. This is the workflow used by the browser UI's "prepare" button.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cfdrunJson` | string | No | JSON string of the `.cfdrun` manifest |
| `cfdrunFilename` | string | No | Manifest filename (default: `project.cfdrun`) |
| `stlFiles` | object | No | Map of `{ "filename.stl": "<base64>" }` STL files |
| `meshOnly` | boolean | No | Append `--mesh-only` to the returned command |
| `solveOnly` | boolean | No | Append `--solve-only` to the returned command |

**Response (200):**

```json
{
  "command": "python -m backend.aircooling.runner.run_cfd --from-cfdrun /path/to/case/model/project.cfdrun",
  "caseDir": "/path/to/case"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/prepare-case \
  -H "Content-Type: application/json" \
  -d '{"meshOnly": true}'
```

---

### `GET /api/server-patches`

**Auth:** Admin or Viewer

List server inlet and outlet patches from the mesh boundary file (`case/Mesh/constant/polyMesh/boundary`). Returns patches whose names contain `_inlet` or `_outlet`.

**Response (200):**

```json
{
  "patches": [
    { "patch": "rack01_inlet", "shortId": "rack01", "type": "inlet" },
    { "patch": "rack01_outlet", "shortId": "rack01", "type": "outlet" },
    { "patch": "acu01_inlet", "shortId": "acu01", "type": "inlet" }
  ]
}
```

**Response (404):**

```json
{ "error": "No mesh found. Run mesh generation first." }
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/server-patches
```

---

### SCANO AI solve: `/api/ai/scano/*`

The solver tab's AI sub-tab (see [scano-ai.md](scano-ai.md)).

| Endpoint | Body / reply |
|---|---|
| `GET /api/ai/scano/models` | `{models: [{id, name, name_zh, description, description_zh, recommended, test_error, ...}], trainingRanges}` — the checkpoints under `src/backend/aircooling/scano/checkpoints/` with their `model_card.json` |
| `GET /api/ai/scano/status` | `{running, device, busy, recent}` — the inference worker |
| `GET /api/gpu-solver/status` | `{enabled, devices, cards [{index, uuid, name, totalMiB, usedMiB, freeMiB, utilization, solver}], mesh {cells, estimateMiB, bestFreeMiB, fits}, message}` — what the solve page shows live while the GPU air solver is selected (`start_server.sh --gpu`): every card of the launch with its memory and utilisation and what the solver does on it (`solving`, `standby` or `null`), the cells of the case's mesh, the memory one solve needs at most (0.8 GiB + 18 GiB per million cells; a solve takes the open connected region of the mesh, so the solver's own check before its time loop decides) and whether the freest card has it. `message` is the restart advice when `fits` is `false`. A solve the solver ended for lack of memory finishes with `solverFailure: {code: "gpu_out_of_memory", error}` in `job-status` and in the final log-stream message |
| `GET /api/ai/scano/gpu?model=&inference=&passes=` | `{running, busy, cpu, gpu {index, uuid, name, totalMiB, usedMiB, freeMiB, utilization, workerMiB}, freeForAiBytes, workerCacheBytes, room {nodes, cells}, plan {mode, reason, fits, estimate_bytes, chunk_share, passes, forwards, wholeBytes, ttaBytes, measured}}` — what the AI tab shows live ([scano-ai.md](scano-ai.md#deployment-whole-room-or-tta-2026-10-29)) |
| `POST /api/ai/scano/warmup` | `{model}` → `{started, warmState}`: start the worker, load the model and run one forward on the case's newest cache state |
| `POST /api/ai/scano/run` | the solve body of `/api/run-mesh` (`cfdrunJson`, `cfdrunFilename`, `stlFiles: {}`) plus `model`, `mode` (`auto` or `exact`), `inference` (`auto`, `whole` or `tta`, default `auto`) and `passes` (1–16) → `{jobId, caseDir, needsFirstIteration}`. A job like a solve (log stream, status, abort); when it is done `job-status` and the final log-stream message carry `scanoResult` `{runDir, runDirRelative, state, changedPatches, nCells, timing, device, warnings, inference {request, mode, reason, passes, chunk, forwards, minCount, estimateBytes, freeBytes, peakBytes, nNodes}}` |

## 5. Job Management

### `GET /api/log-stream`

**Auth:** Admin or Viewer

Stream real-time log output from a running or completed job using **Server-Sent Events (SSE)**. Each event carries a single JSON-encoded log line. When the job completes, a final event is emitted with `done: true`.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | Yes | Job ID returned by `/api/run-mesh`, `/api/run-check-mesh`, or `/api/browser/full-pipeline` |

**Response Content-Type:** `text/event-stream`

**Event format (log line):**

```
data: "2026-04-23 10:00:00 Meshing started..."

```

**Final event (job complete):**

```
data: {"done": true, "returncode": 0, "checkMeshOk": null, "failedChecks": null}

```

**Final event (checkMesh job complete):**

```
data: {"done": true, "returncode": 0, "checkMeshOk": true, "failedChecks": 0, "meshWarnings": []}

```

**Example:**

```bash
curl -b cookie.txt \
  "http://localhost:$PORT/api/log-stream?job_id=a1b2c3d4"
```

---

### `GET /api/job-status`

**Auth:** Admin or Viewer

Poll the status of a job without streaming. Returns whether the job is done, the exit code, and the current log line count. For checkMesh jobs, also returns `checkMeshOk` and `failedChecks`.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | Yes | Job ID to query |

**Response (200):**

```json
{
  "jobId": "a1b2c3d4",
  "done": false,
  "returncode": null,
  "lineCount": 42,
  "checkMeshOk": null,
  "failedChecks": null
}
```

**Response (200 — completed checkMesh):**

```json
{
  "jobId": "b2c3d4e5",
  "done": true,
  "returncode": 0,
  "lineCount": 156,
  "checkMeshOk": true,
  "failedChecks": 0
}
```

**Response (404):**

```json
{ "error": "job not found" }
```

**Example:**

```bash
curl -b cookie.txt "http://localhost:$PORT/api/job-status?job_id=a1b2c3d4"
```

---

### `POST /api/abort-job`

**Auth:** Admin

Send `SIGTERM` to the process group of a running job (catches `mpirun` children too), then fall back to `SIGKILL`. If the job has already completed, returns `status: already_done`.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `jobId` | string | Yes | Job ID to abort |

**Response (200 — aborted):**

```json
{ "status": "aborted", "jobId": "a1b2c3d4" }
```

**Response (200 — already done):**

```json
{ "status": "already_done", "jobId": "a1b2c3d4" }
```

**Response (404):**

```json
{ "error": "job not found" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/abort-job \
  -H "Content-Type: application/json" \
  -d '{"jobId": "a1b2c3d4"}'
```

---

### `GET /api/scan-case-status`

**Auth:** Admin or Viewer

Scan the case directory for mesh readiness and all available solve runs. Returns whether post-processing is available based on whether any run directory contains results.

**Response (200):**

```json
{
  "meshReady": true,
  "logRuns": [
    {
      "name": "2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam",
      "path": "/path/to/case/log/2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam",
      "timesteps": [0, 50, 100, 200, 500],
      "latestTimestep": 500,
      "hasResults": true
    }
  ],
  "solveReady": true,
  "postprocessReady": true
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/scan-case-status
```

---

## 6. Post-Processing

Post-processing endpoints read OpenFOAM results from the run directory initialized by `/api/pp/init`. Most binary response endpoints return `application/octet-stream` with a header JSON blob followed by interleaved float32 data.

The loaded result is a process-wide snapshot. Successful `/api/pp/init` returns
`runId`, actual `timeValue`, and opaque `revision`. Supply its revision as
`X-PP-Revision` on `/api/pp/mesh`, `/mesh/binary`, `/field/{name}`, `/slice`,
`/streamlines`, `/volume`, `/ai/start`, and `/ai/infer`. Missing revision returns
428 (`pp_revision_required`); an older tab's revision returns 409
(`pp_revision_stale`) without sending another run's data. Mesh JSON includes
the same identity; binary responses echo `X-PP-Revision`. AI field injection
returns a new revision, which the caller must use from then on. The caller
must also compare `runId` and `timeValue` with its selected result. Run listing,
AI status and the demo-model-path lookup do not require this header.

### `POST /api/pp/init`

**Auth:** Admin or Viewer (one of the four POST endpoints a viewer may call)

Initialize the post-processor for a specific run directory. Loads the OpenFOAM mesh and available fields via PyVista. Must be called before any other `/api/pp/*` endpoint (except `/api/pp/runs`).

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `runDir` | string | No | Relative path from the current case directory to the run (e.g. `log/2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam`). A case-internal absolute path is also accepted for explicit `.vtm` imports. Resolved paths outside the selected case directory return 400 (`pp_run_outside_case`). If omitted, the latest run directory with a polyMesh is used. |
| `timeValue` | number | No | Exact OpenFOAM time directory to load. Omission selects the latest field-bearing solved time; `0` explicitly loads initial fields when present. A time absent from any mesh partition returns 400 instead of falling back to another step. This checks time-directory availability, not that every possible solver field finished writing. Not accepted for a VTM file. |
| `timeout` | number | No | Initialization timeout in seconds (default: `3600`) |

**Response (200):**

```json
{
  "success": true,
  "sourceFormat": "openfoam",
  "timeValue": 100,
  "runId": "sha256-of-canonical-run-path",
  "revision": "opaque-loaded-snapshot-token",
  "cached": false,
  "n_cells": 500,
  "message": "openfoam loaded, 3 patches, 8 fields, 500 cells"
}
```

**Response (404):**

```json
{ "success": false, "error": "No run directories found" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/init \
  -H "Content-Type: application/json" \
  -d '{"runDir": "log/2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam", "timeValue": 100}'
```

---

### `GET /api/pp/mesh`

**Auth:** Admin or Viewer

Return patch metadata as JSON. Requires `/api/pp/init` first.

**Response (200):**

```json
{
  "patches": [
    {
      "name": "room_wall",
      "nPoints": 12345,
      "nTriangles": 24688,
      "bounds": [0.0, 0.0, 0.0, 10.0, 5.0, 3.0]
    }
  ],
  "fields": ["T", "U", "p", "p_rgh"],
  "totalPoints": 98765,
  "totalTriangles": 197528,
  "runId": "sha256-of-canonical-run-path",
  "timeValue": 100,
  "revision": "opaque-loaded-snapshot-token"
}
```

**Example:**

```bash
curl -b cookie.txt -H "X-PP-Revision: <init revision>" http://localhost:$PORT/api/pp/mesh
```

---

### `GET /api/pp/mesh/binary`

**Auth:** Admin or Viewer

Return mesh geometry as a binary `ArrayBuffer` (`application/octet-stream`). Requires `/api/pp/init` first. Used by the 3D viewport for rendering boundary surfaces.

**Response Content-Type:** `application/octet-stream`

**Binary format:** Interleaved float32 arrays (positions, normals) packed by `CachedPostProcessor.get_mesh()`. The layout is determined by the frontend Three.js renderer and should be consumed as a `Float32Array`.

**Example:**

```bash
curl -b cookie.txt -H "X-PP-Revision: <init revision>" http://localhost:$PORT/api/pp/mesh/binary -o mesh.bin
```

---

### `GET /api/pp/field/{name}`

**Auth:** Admin or Viewer

Return scalar field values as a binary `ArrayBuffer`. The scalar range and field metadata are returned in the `X-Field-Info` response header as a JSON string.

**URL parameter:** `name` — field name, e.g. `T`, `U`, `p`, `p_rgh`, `rho`, `Ux`, `Uy`, `Uz`

**Response Content-Type:** `application/octet-stream`

**Response Header:**
```
X-Field-Info: {"field": "T", "min": 293.15, "max": 333.15, "nPoints": 98765, "isVector": false}
```

**Binary format:** One `float32` per mesh point, in the same point order as `/api/pp/mesh/binary`.

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/pp/field/T \
  -H "X-PP-Revision: <init revision>" \
  -D - -o field_T.bin
```

---

### `GET /api/pp/runs`

**Auth:** Admin or Viewer

List all available run directories under `case/log/`. Does not require `/api/pp/init`.

**Response (200):**

```json
{
  "runs": [
    {
      "name": "2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam",
      "path": "log/2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam"
    }
  ]
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/pp/runs
```

---

### `POST /api/pp/streamlines`

**Auth:** Admin

Compute 3D streamlines on the internal mesh using velocity field `U`. Seeds are distributed uniformly in a sphere. Returns binary data: a header JSON blob (4-byte little-endian length prefix, padded to 4-byte alignment) followed by interleaved `float32` data `[x, y, z, scalar, ...]` for each streamline point.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `center` | [x, y, z] | No | Seed sphere center (default: mesh center) |
| `radius` | number | No | Seed sphere radius in meters (default: `1.0`) |
| `nSeeds` | integer | No | Number of seed points (default: `27`) |
| `field` | string | No | Scalar field for coloring: `"speed"` (velocity magnitude) or any field name (default: `"speed"`) |

**Response Content-Type:** `application/octet-stream`

**Binary format:**
```
[4 bytes: uint32 header_length]
[header_length bytes: JSON header, padded to 4-byte boundary]
[remaining bytes: float32 interleaved data: x,y,z,scalar per point]
```

**Header JSON:**
```json
{
  "streamlines": [{"nPoints": 128}, {"nPoints": 64}],
  "scalarRange": [0.0, 2.5],
  "field": "speed"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/streamlines \
  -H "Content-Type: application/json" \
  -H "X-PP-Revision: <init revision>" \
  -d '{"center": [5.0, 2.5, 1.5], "radius": 2.0, "nSeeds": 50, "field": "speed"}' \
  -o streamlines.bin
```

---

### `POST /api/pp/slice`

**Auth:** Admin

Slice the internal mesh with a plane and return the surface geometry + scalar field values as binary data. Uses the same binary format as `/api/pp/streamlines` (4-byte header length prefix, JSON header, then float32 data).

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `normal` | [nx, ny, nz] | No | Plane normal vector (default: `[0, 0, 1]` — horizontal slice) |
| `origin` | [x, y, z] | No | A point on the plane (default: mesh center) |
| `field` | string | No | Scalar field to sample on the slice (default: `"T"`) |

**Response Content-Type:** `application/octet-stream`

**Binary format:** Same as `/api/pp/streamlines` — header JSON (with patch geometry info) followed by interleaved float32 positions and scalars.

**Example:**

```bash
# Horizontal slice at z=1.5 m, temperature field
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/slice \
  -H "Content-Type: application/json" \
  -H "X-PP-Revision: <init revision>" \
  -d '{"normal": [0, 0, 1], "origin": [5.0, 2.5, 1.5], "field": "T"}' \
  -o slice.bin
```

---

### `POST /api/pp/volume`

**Auth:** Admin

Resample the internal mesh to a regular 3D grid for volume rendering. Returns binary data in the same format (header JSON + float32 voxel data). Requires `/api/pp/init` first.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `field` | string | No | Scalar field to resample (default: `"T"`) |
| `resolution` | integer | No | Grid resolution per axis (default: `128`; larger values are slower) |

**Response Content-Type:** `application/octet-stream`

**Binary format:** Header JSON with grid metadata (`dims`, `bounds`, `scalarRange`) followed by `float32` voxel values in row-major (C) order.

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/volume \
  -H "Content-Type: application/json" \
  -H "X-PP-Revision: <init revision>" \
  -d '{"field": "T", "resolution": 64}' \
  -o volume.bin
```

---

### `GET /api/pp/ai/status`

**Auth:** Admin or Viewer

Return the status of the persistent AI inference worker process (running in the `FVGN-pt2.7` conda environment). Reports whether the model is loaded, which device it is on, and recent stderr lines.

**Response (200 — worker not started):**

```json
{
  "ready": false,
  "device": "",
  "modelPath": "",
  "casePath": "",
  "caseName": "",
  "bounds": null,
  "lastError": null,
  "lastResult": null,
  "stderr": []
}
```

**Response (200 — worker ready):**

```json
{
  "ready": true,
  "device": "cuda:0",
  "modelPath": "/path/to/model.pt",
  "casePath": "/path/to/case/log/...",
  "caseName": "MyRoom",
  "bounds": {"min": [0, 0, 0], "max": [10, 5, 3]},
  "lastError": null,
  "lastResult": null,
  "stderr": []
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/pp/ai/status
```

---

### `POST /api/pp/ai/start`

**Auth:** Admin

Load the AI model into the persistent worker process and warm it up. The `casePath` must match the run directory currently loaded by `/api/pp/init`. The worker reads an independent `case/h5/*.h5` graph and does **not** receive the selected OpenFOAM `timeValue`; its prediction must not be presented as a validated prediction for that CFD frame. The response explicitly reports `predictionSource: "independent_h5_graph"` and `timeConditioned: false`. Model loading can take several minutes on first call.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `modelPath` | string | Yes | Absolute path to the trained model file (`.pt`) |
| `casePath` | string | Yes | Absolute path or path relative to the selected case directory; it must resolve to the run loaded by the current pp session. |

**Response (200):**

```json
{
  "success": true,
  "runId": "sha256-of-canonical-run-path",
  "timeValue": 100,
  "revision": "same-init-revision",
  "predictionSource": "independent_h5_graph",
  "timeConditioned": false,
  "status": { "ready": true, "device": "cuda:0", "modelPath": "..." },
  "result": {},
  "stderr": []
}
```

**Response (400 — path mismatch):**

```json
{
  "error": "AI case path must match current postprocess run: /path/to/current/run"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/ai/start \
  -H "Content-Type: application/json" \
  -H "X-PP-Revision: <init revision>" \
  -d '{
    "modelPath": "/home/user/models/fvgn_v2.pt",
    "casePath": "/path/to/case/log/2026-04-23-10-00-00_buoyantBoussinesqSimpleFoam"
  }'
```

---

### `POST /api/pp/ai/infer`

**Auth:** Admin

Run AI inference on a user-specified bounding box region. Injects the predicted fields (`AI_U`, `AI_p`, `AI_T`, `AI_region_mask`) into the cached post-processor for comparison alongside CFD fields. The AI fields come from the separate H5 graph; result revision binding prevents cross-tab injection but does not make them time-matched to the selected CFD frame.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `box` | object | Yes | Bounding box with `min: [x, y, z]` and `max: [x, y, z]` |

**Response (200):**

```json
{
  "success": true,
  "runId": "sha256-of-canonical-run-path",
  "timeValue": 100,
  "revision": "new-revision-after-injection",
  "predictionSource": "independent_h5_graph",
  "timeConditioned": false,
  "result": {
    "outputPath": "/tmp/ai_output_abc123.npz",
    "inferenceTimeMs": 342
  },
  "fields": ["T", "U", "p", "p_rgh", "AI_T", "AI_U", "AI_p", "AI_region_mask"],
  "stderr": []
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/pp/ai/infer \
  -H "Content-Type: application/json" \
  -H "X-PP-Revision: <init revision>" \
  -d '{"box": {"min": [2.0, 1.0, 0.0], "max": [8.0, 4.0, 3.0]}}'
```

---

### `GET /api/pp/ai/demo-model-path`

**Auth:** Admin or Viewer

The demo AI checkpoint path from `DCDTUNI_AI_DEMO_MODEL`, if set (used by the **Demo Weights** button); empty otherwise.

---

### `GET /api/pp/render-source` and `POST /api/pp/render-grant`

**Auth:** `render-source` Admin or Viewer; `render-grant` Admin or Viewer (one of the four POST routes a viewer may call). Both require `X-PP-Revision`.

`render-source` verifies that the loaded result comes from an ordinary executed run on the checked mesh (its quality verdict is reported, not required) and returns its identity. `render-grant` takes the editor's display intent (fields, slices, streamlines, …) and signs the server-owned numeric products for the Omniverse bridge; without the launcher's `--ov` key it returns `503 display_grant_unavailable`.

---

### `GET /api/probes-latest`

**Auth:** Admin or Viewer

Return the latest probe monitoring data (temperature, velocity, pressure) from the current solve run's `postProcessing/probes/` directory. Returns incremental data since `since` step to avoid re-sending data the client already has. Only reads probes from the most recently started solve run (identified by `_LATEST_SOLVE_PREFIX`), so stale data from previous runs is never returned during a live solve.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `since` | integer | No | Only return steps > this value (default: `-1` = return all) |

**Response (200):**

```json
{
  "steps": [50, 100, 150, 200],
  "writeInterval": 50,
  "fields": {
    "T": [[300.1, 301.5], [300.8, 302.1]],
    "U": [[[0.1, 0.0, 0.5], [0.2, 0.1, 0.4]], [[0.15, 0.0, 0.6], [0.25, 0.1, 0.5]]],
    "p": [[0.01, -0.01], [0.02, -0.02]],
    "p_rgh": [[0.0, 0.0], [0.01, -0.01]],
    "rho": [[1.19, 1.18], [1.19, 1.18]]
  }
}
```

Each inner list represents values at that timestep for each probe location. For `U`, each value is a `[ux, uy, uz]` vector. Fields missing from the case (e.g. `rho` for Boussinesq solvers) will have empty lists.

**Example:**

```bash
curl -b cookie.txt "http://localhost:$PORT/api/probes-latest?since=100"
```

---

### `POST /api/harvest-sensor-results`

**Auth:** Admin

Read the final probe row from the current run's probe files, convert temperatures to the requested unit, and write a structured JSON sensor-results file to `case/log/<run>/sensorResults/<caseName>_sensor_<time>.json`.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `caseName` | string | No | Name prefix for the output file (default: `"case"`) |
| `sensorNames` | array | No | List of sensor name strings (must match probe count) |
| `unitTemperature` | string | No | Temperature unit: `"C"`, `"K"`, or `"F"` (default: `"C"`) |

**Response (200):**

```json
{ "path": "/path/to/case/log/.../sensorResults/MyRoom_sensor_14-30-00.json" }
```

**Response (500):**

```json
{ "error": "sensor count mismatch: frontend=5, probes=3" }
```

**Output file format:**

```json
{
  "version": "1.0",
  "caseName": "MyRoom",
  "solveRunDir": "log/2026-04-23-10-00-00_...",
  "finalStep": 500,
  "writeInterval": 50,
  "unitLength": "m",
  "unitTemperature": "C",
  "generatedAt": "2026-04-23T14:30:00",
  "sensors": [
    {
      "name": "sensor_rack01",
      "location": [3.0, 2.0, 1.0],
      "T_final": 35.2,
      "U_final": [0.12, 0.0, 0.45],
      "p_final": 0.02,
      "p_rgh_final": 0.0,
      "rho_final": null
    }
  ]
}
```

Values that fall outside the mesh (OpenFOAM sentinel `< -1e299`) are returned as `null`.

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/harvest-sensor-results \
  -H "Content-Type: application/json" \
  -d '{"caseName": "MyRoom", "sensorNames": ["inlet", "outlet"], "unitTemperature": "C"}'
```

---

## 7. Liquid Cooling

The liquid solver the editor uses is the Newton DAE, run in server-owned **runtime sessions** (`/api/liquid/runs*`). The older one-shot endpoints remain for compatibility.

### `/api/liquid/runs` — runtime sessions

**Auth:** Admin (GET also Viewer)

| Method and path | Purpose |
|---|---|
| `POST /api/liquid/runs` | Create a run from `{liquidrun, controllers?, checkpoint?, project_revision?}`; returns its id and status (201). The case directory is captured at creation. |
| `POST /api/liquid/runs/{id}/start` · `/resume` | Run (optional `{"speed": null \| <sim s per wall s>}`) |
| `POST /api/liquid/runs/{id}/pause` · `/step` · `/cancel` | Lifecycle control |
| `POST /api/liquid/runs/{id}/inputs` | Online commands for the next accepted step: `heat_multiplier`, `pump_rpm_ratios`, `valve_openings`, `primary_inlet_K` |
| `POST /api/liquid/runs/{id}/equilibrium` | Independent steady state for the pending inputs (optional `heat_multiplier`), without changing the trajectory |
| `GET /api/liquid/runs/{id}` | Status |
| `GET /api/liquid/runs/{id}/frames?after=N` | Frames with sequence number > N |
| `GET /api/liquid/runs/{id}/result` · `/checkpoint` · `/executed-input` | Result, resumable checkpoint, standalone executed `.liquidrun` |
| `GET /api/liquid/runs/{id}/heat-audit?project_uid=<uid>` | Liquid heat ledger for an air handoff |
| `GET /api/liquid/runs` | The runs of the selected case, saved on disk and loaded in this server: `{case, runs}` |
| `GET /api/liquid/runs/{id}/saved` | A saved run's header, record metadata and accepted-input receipt, read from `Liquid/log/run-<id>/` |
| `POST /api/liquid/runs/{id}/restore` | Load a saved run back as a read-only session (body `{}`), e.g. after a server restart; a GET on a run that is saved but not loaded answers 404 with `"saved": true` and this path |

Invalid transitions return 409, invalid input 400, unknown runs 404. Artifacts are written to `<caseDir>/Liquid/log/run-<id>/`. Details and examples: [Liquid runtime API](liquid-runtime-api.md).

### `POST /api/liquid/export-liquidrun`

**Auth:** Admin

Save the frontend `.liquidrun` JSON model to disk. The request body must be the full `.liquidrun` JSON object (as built by the frontend liquid editor). The file is written to `<caseDir>/Liquid/model/<projectName>.liquidrun`, creating the directory if needed.

**Request:** Full `.liquidrun` JSON object. The `projectName` field (string) within the body is used as the output filename stem; it defaults to `"unnamed"` if absent.

**Response (200):**

```json
{
  "saved": "/path/to/case/Liquid/model/MyProject.liquidrun",
  "projectName": "MyProject"
}
```

**Response (400):**

```json
{ "error": "No case directory set" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/liquid/export-liquidrun \
  -H "Content-Type: application/json" \
  -d '{"projectName": "MyProject", "nodes": [], "edges": []}'
```

---

### `POST /api/liquid/solve`

**Auth:** Admin

One-shot solve of the **latest `.liquidrun` file** in `<caseDir>/Liquid/model/`, streaming progress as **Server-Sent Events** (the connection stays open for the duration of the solve). The input decides the solver: a DAE input (`.liquidrun` v3/v4) runs the Newton DAE (`device` must be omitted or match the manifest's execution device); v2 runs the runtime/PID path on CPU; an unversioned v1 input runs the original direct solver. The editor uses the runtime sessions above instead.

**Request:** Optional JSON body.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `device` | string | No | Torch device (default `"cpu"`). For DAE inputs the device comes from the manifest. |
| `monitor_cps` | array | No | List of cold-plate IDs to monitor during the solve (default: empty — monitor none) |

**Response Content-Type:** `text/event-stream`

The server emits SSE `data:` events in this order:

1. `{"stage": "compiling"}` — sent immediately, before the solver starts.
2. Periodic solver progress log objects — emitted via the solver callback while iterating (one per logged epoch).
3. A terminal `{"stage": "done", …}` event on success; the fields depend on the solver path (the v1 direct solver reports `converged`, `final_loss`, `epochs`).

On failure, a single `{"stage": "error", "message": "...", "traceback": "..."}` event is sent instead of the `done` event.

**Event stream example:**

```
data: {"stage": "compiling"}

data: {"epoch": 100, "loss": 1.2e-03}

data: {"stage": "done", "converged": true, "final_loss": 4.7e-06, "epochs": 800}

```

**Response (500 — backend missing):**

```json
{ "error": "dcdtuni not available" }
```

**Response (400 — no case directory):**

```json
{ "error": "No case directory set" }
```

**Response (404 — no model):**

```json
{ "error": "No .liquidrun file found in <caseDir>/Liquid/model" }
```

**Example:**

```bash
# streaming SSE
curl -b cookie.txt -N -X POST http://localhost:$PORT/api/liquid/solve \
  -H "Content-Type: application/json" \
  -d '{"monitor_cps": ["cp_rack01_0"]}'
```

---

### `POST /api/liquid/transient` (retired)

**Auth:** Admin

Returns **HTTP 410 Gone** with a `Deprecation: true` header, a `Link: </api/liquid/runs>; rel="successor-version"` header and `{"code": "endpoint_retired", "deprecated": true, "error", "replacement"}`. Liquid transients are Newton DAE runtime sessions with `analysis.mode = "transient"`: `POST /api/liquid/runs`, then `/start`, `/pause`, `/step` and `/frames`. The route stays so older clients get this answer instead of a missing route.

---

### `GET /api/liquid/results`

**Auth:** Admin or Viewer

Read liquid cooling results of the current case. The handler looks under `<caseDir>/Liquid/log/` and then `<caseDir>/log/` for runtime session results (`run-<id>/result.json`) and older `liquid_result.json` files, newest first. Returns 404 if no results have been computed yet.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `run` | string | No | Name of a specific run directory to read. Without it, the most recent result directory is returned. |

**Response (200):**

```json
{
  "servers": {
    "server_rack01_0": {
      "server_total_power_W": 500.0,
      "liquid_flow_rate_LPM": 3.2,
      "chip_max_temp_C": 65.3
    }
  }
}
```

**Response (404):**

```json
{ "error": "No liquid results found" }
```

**Example:**

```bash
# Most recent result
curl -b cookie.txt http://localhost:$PORT/api/liquid/results

# A specific run directory
curl -b cookie.txt "http://localhost:$PORT/api/liquid/results?run=2026-05-18-10-00-00_odil"
```

---

### `POST /api/run-coupled-sim` (retired)

**Auth:** Admin

Returns **HTTP 410 Gone** with the same headers and body shape as `/api/liquid/transient`. It ran the removed ODIL batch solve and relied on the air runner picking up `liquid_results.json`; neither exists any more. Air–liquid coupling goes through `/api/run-mesh` with a `hybridHeat` body (see §4): the server audits a completed liquid runtime session and compiles its heat into the air manifest.

---

### `GET /api/hybrid/heat-ledger`

**Auth:** Admin or Viewer

The liquid-to-air heat ledger of the selected case, `model/heat-ledger.json`. The server writes it beside the derived hybrid `.cfdrun` when an air run is published with `hybridHeat` from a completed liquid run, and deletes it on any ordinary export. The response carries the ledger, its path and modification time, and the active `.cfdrun` (`filename`, `run_id`) with whether the ledger belongs to it (`matches_active_cfdrun`; false only when the files were edited outside the server). 404 `heat_ledger_missing` when there is none; 409 `heat_ledger_unreadable` when the file is not a ledger.

---

### `POST /api/ov/liquid-result-grant`

**Auth:** Admin

`{runId}` → a server-signed display grant for a DAE run of the selected case (`Liquid/log/run-<id>/result.json`). The editor relays it to the Omniverse bridge, which re-hashes the file, binds it to the acknowledged scene and colours the liquid parts. Results without object bindings are refused with the missing keys. Requires the launcher's `--ov` bridge.

---

## 8. Browser Automation

The `/api/browser/*` endpoints control a persistent Chromium browser (via Playwright) owned by the server. It opens the same page a user opens — the React editor in `--app react`, `STL_generate.html` in `--app html` — and logs in with a random worker session. This allows fully automated workflows: load a project file, export STL geometry, and trigger mesh generation — all without user interaction. In `--app api` mode these routes return 409 (there is no page), except `/api/browser/close`.

The browser starts lazily on the first `/api/browser/*` call and waits until the page's project loader is ready; later calls are fast. By default it is a head browser on the X display `:99` (`DCDTUNI_BROWSER_DISPLAY`) rendering WebGL on the NVIDIA GPU; `DCDTUNI_BROWSER_SOFTWARE=1` switches to software WebGL (headless, no GPU) for export-only runs, and `DCDTUNI_BROWSER_HEADLESS` overrides the head/headless choice. The browser is released before a CFD job starts.

In the React editor, scripts should use the automation contract `window.__dcdtuni` (`src/frontend/react/src/automation/contract.ts`) rather than internal state; the legacy page exposes its `window.*` functions and `state`.

### `POST /api/browser/close`

**Auth:** Admin

Close the server's automation browser if one is running; never starts one. Returns 200 when closed, 202 while closing. Allowed in `--app api` mode.

---

### `POST /api/browser/exec-js`

**Auth:** Admin

Execute arbitrary JavaScript in the browser page context and return the result. The page keeps its project (models, undo history, etc.) between calls.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `js` | string | Yes | JavaScript expression to evaluate (must return a JSON-serializable value) |

**Response (200):**

```json
{ "result": 42 }
```

**Response (500 — JS error):**

```json
{ "error": "ReferenceError: foo is not defined" }
```

**Example:**

```bash
# Create a box through the automation contract (React editor); returns its uid
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/exec-js \
  -H "Content-Type: application/json" \
  -d '{"js": "window.__dcdtuni.createDefaultBaseModel()"}'
```

---

### `POST /api/browser/load-proj`

**Auth:** Admin

Load a `.cfdproj` project file from the server's file system into the browser page through the page's own project loader (the same path as **Load Project**: format check, migration to the current version, scene rebuild).

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | Yes | Absolute path to the `.cfdproj` file on the server |

**Response (200 — success):**

```json
{
  "success": true,
  "modelCount": 15,
  "renderMeshes": 15
}
```

**Response (200 — failure):**

```json
{
  "success": false,
  "error": "JSON.parse failed: unexpected token"
}
```

**Response (404):**

```json
{ "error": "File not found: /path/to/missing.cfdproj" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/load-proj \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/user/projects/MyDataCenter.cfdproj"}'
```

---

### `POST /api/browser/click`

**Auth:** Admin

Simulate a click in the headless browser using a CSS selector or button text.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `selector` | string | No | CSS selector (e.g. `"#runMeshBtn"`) |
| `text` | string | No | Button text to match (e.g. `"Run Mesh"`) |

Provide exactly one of `selector` or `text`.

**Response (200):**

```json
{ "success": true, "selector": "#runMeshBtn" }
```

**Response (200 — element not found):**

```json
{ "success": false, "selector": "#missing", "error": "TimeoutError: waiting for locator..." }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/click \
  -H "Content-Type: application/json" \
  -d '{"text": "导出 STL"}'
```

---

### `POST /api/browser/export-stl`

**Auth:** Admin

Generate STL geometry files in the headless browser (calls `buildGeometryData()` then `buildGroupedStlMap()`) and write them to `case/Mesh/constant/triSurface/`. Stale STL artifacts from previous runs are removed first.

**Request:** Empty body

**Response (200):**

```json
{
  "success": true,
  "filesWritten": ["merged_rack.stl", "room_wall.stl", "merged_acu.stl"],
  "count": 3,
  "triSurfaceDir": "/path/to/case/Mesh/constant/triSurface"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/export-stl
```

---

### `POST /api/browser/export-cfdrun`

**Auth:** Admin

Export the `.cfdrun` manifest JSON from the automation browser, built by the page's own exporter (in the React editor the v4 manifest of `buildManifestV2`). This is the same data that would be written to `case/model/<name>.cfdrun` by other endpoints. The old path `/api/browser/export-cfd` is an alias.

**Deprecated alias:** `/api/browser/export-cfd` is accepted as a silent deprecated alias for this handler (identical behavior, no warning emitted). Prefer `/api/browser/export-cfdrun` for new code.

**Request:** Empty body

**Response (200):**

```json
{
  "success": true,
  "data": {
    "version": "2.3.2",
    "room": { ... },
    "models": [ ... ]
  }
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/export-cfdrun
```

---

### `POST /api/browser/export-case`

**Auth:** Admin

Combine load-proj + export-stl + export-cfdrun into a single atomic call. Loads the specified `.cfdproj`, exports all STL files to `case/Mesh/constant/triSurface/`, and writes the `.cfdrun` manifest to `case/model/`. Does **not** trigger mesh generation.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `projPath` | string | Yes | Absolute path to the `.cfdproj` file on the server |

**Response (200):**

```json
{
  "success": true,
  "modelCount": 15,
  "stlFiles": ["merged_rack.stl", "room_wall.stl", "merged_acu.stl"],
  "stlCount": 3,
  "triSurfaceDir": "/path/to/case/Mesh/constant/triSurface",
  "cfdrunPath": "/path/to/case/model/MyDataCenter.cfdrun"
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/export-case \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/home/user/projects/MyDataCenter.cfdproj"}'
```

---

### `POST /api/browser/full-pipeline`

**Auth:** Admin

One-shot end-to-end pipeline: load project → export STL → export `.cfdrun` → launch mesh+solve job. Returns a `jobId` that can be monitored with `/api/log-stream`. This is the recommended endpoint for automated batch processing.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `projPath` | string | Yes | Absolute path to the `.cfdproj` file on the server |
| `meshOnly` | boolean | No | Run mesh generation only (default: `false`) |
| `solveOnly` | boolean | No | Run solve only on existing mesh (default: `false`) |

**Response (200):**

```json
{
  "success": true,
  "jobId": "c3d4e5f6",
  "stlFiles": ["merged_rack.stl", "room_wall.stl"],
  "modelsLoaded": 15
}
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/browser/full-pipeline \
  -H "Content-Type: application/json" \
  -d '{"projPath": "/home/user/projects/MyDataCenter.cfdproj", "meshOnly": true}'
```

---

### `GET /api/browser/state`

**Auth:** Admin or Viewer

Get a summary of the current headless browser state (model list, unit settings, CFD room config).

**Response (200):**

```json
{
  "modelCount": 15,
  "models": [
    {
      "id": "model_001",
      "kind": "rack",
      "name": "Rack A1",
      "enabled": true,
      "anchor": [1.0, 0.5, 0.0],
      "base": [0.6, 1.0, 2.0],
      "patchCount": 8,
      "cfd": { "type": "rack", "totalPowerW": 5000 },
      "equipmentCount": 44
    }
  ],
  "inputUnit": "m",
  "selection": null,
  "cfdRoom": { "width": 20.0, "depth": 15.0, "height": 3.0 }
}
```

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/browser/state
```

---

### `GET /api/browser/geometry-status`

**Auth:** Admin or Viewer

Diagnostic endpoint to check whether the headless browser's geometry engine is functioning correctly (WebGL availability, render mesh count, model count).

**Response (200):**

```json
{
  "webglAvailable": false,
  "renderMeshesCount": 15,
  "modelsLoaded": 15,
  "rendererExists": false
}
```

Note: `webglAvailable` is always `false` in headless mode (no GPU). `renderMeshesCount` should equal `modelsLoaded` if geometry was built successfully.

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/browser/geometry-status
```

---

### `GET /api/browser/logs`

**Auth:** Admin or Viewer

Retrieve the headless browser's JavaScript console logs and page errors. Optionally clear the log buffer.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `clear` | string | No | Set to `"true"` to clear the log buffer after reading |

**Response (200):**

```json
{
  "logs": [
    "[log] buildGeometryData: 15 models",
    "[warn] Missing texture: rack_front.png"
  ],
  "errors": [
    "[PAGE_ERROR] TypeError: Cannot read property 'length' of undefined"
  ],
  "totalLogs": 42
}
```

Returns the last 100 log entries and all errors.

**Example:**

```bash
curl -b cookie.txt "http://localhost:$PORT/api/browser/logs?clear=true"
```

### `GET /api/browser/screenshot`

**Auth:** Any authenticated user

Takes a screenshot of the headless browser viewport.

**Response:**

Content-Type: `image/png`

Returns a PNG image of the current 3D viewport state.

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/browser/screenshot -o viewport.png
```

---

## 9. Utility & Preview

### `GET /api/server-info`

**Auth:** Admin or Viewer

Return server capabilities and configuration. Used by the frontend to check whether dcdtuni is available and which case directory is set.

**Response (200):**

```json
{
  "dctwinAvailable": true,
  "dctwinVersion": "4.0.1",
  "pythonExecutable": "/path/to/dcdtuni/.codex-tmp/envs/dcdtuni/bin/python",
  "caseDir": "/path/to/case",
  "caseDirOk": true,
  "allOk": true,
  "cpuCount": 16,
  "wsPort": 8001,
  "ovSignalingHost": "…",
  "ovSignalingPort": 8301,
  "ovBridge": false,
  "authRequired": true,
  "repoRoot": "/path/to/dcdtuni",
  "app": "react"
}
```

`wsPort` is where the optional admin terminal would listen; the response does not say whether `--enable-terminal` was given. `ovBridge` is true when the launcher started the Omniverse bridge (`--ov`).

---

### `GET /api/ov-status`

**Auth:** Admin or Viewer

Whether the Omniverse display bridge is configured and whether Kit's signalling port is listening yet (Kit takes about two minutes to start). The editor polls it to show "starting" and connects once Kit is ready.

```json
{ "bridge": true, "port": 8301, "host": "…", "kitListening": true }
```

---

### `POST /api/ov/result-dataset`

**Auth:** Admin

`{runId}` → converts an air run of the selected case for the Omniverse bridge and returns a server-signed display grant. The browser relays the grant to Kit unchanged; Kit verifies it with the key the launcher gave both processes, so no browser-supplied path is opened. Requires `--ov`.

---

### `POST /api/render-scene/export`

**Auth:** Admin

Persists an explicitly derived render frame (the scene snapshot both renderers draw) under the selected case. Request bodies are limited to 64 MiB. See [Render scene contract](render-scene-contract.md).

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/server-info
```

---

### `POST /api/screenshot`

**Auth:** Admin

Save a base64-encoded PNG screenshot (from the browser's `canvas.toDataURL()`) to `case/screenshots/ui_screenshot_<timestamp>.png`. Used by the "📸 截图" button in the Operation Log bar.

**Request:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `imageData` | string | Yes | Data URL string: `data:image/png;base64,<base64>` (the `data:...;base64,` prefix is stripped automatically) |

**Response (200):**

```json
{
  "path": "/path/to/case/screenshots/ui_screenshot_20260423_143000.png",
  "timestamp": "20260423_143000"
}
```

**Example:**

```bash
# In the browser (JavaScript):
# const dataUrl = canvas.toDataURL('image/png');
# fetch('/api/screenshot', { method: 'POST', body: JSON.stringify({ imageData: dataUrl }) });
```

---

### `POST /api/mesh-preview/load`

**Auth:** Admin

Load the mesh boundary surfaces from `case/Mesh/` into a `MeshPreviewLoader` instance for the 3D mesh preview panel. Returns patch metadata and cell/face counts. Does not require a prior `/api/pp/init` call.

**Request:** Empty body

**Response (200):**

```json
{
  "patches": [
    { "name": "room_wall", "nPoints": 5000, "nTriangles": 9998, "nEdges": 14997 }
  ],
  "totalPoints": 45000,
  "totalTriangles": 89982,
  "totalEdges": 134973,
  "nCells": 1250000,
  "nBoundaryFaces": 89982,
  "nBoundarySum": 89982,
  "meshSnapshotKey": "64-hex-stat-snapshot-key"
}
```

**Response (404):**

```json
{ "error": "No mesh found at /path/to/case/Mesh" }
```

**Example:**

```bash
curl -b cookie.txt -X POST http://localhost:$PORT/api/mesh-preview/load
```

---

### `GET /api/mesh-preview/binary`

**Auth:** Admin or Viewer

Return the mesh preview geometry as a binary `ArrayBuffer` (`application/octet-stream`). Requires a prior `/api/mesh-preview/load` call. Uses the same float32 format as `/api/pp/mesh/binary`.

New clients send the returned `meshSnapshotKey` as a query parameter. A changed
case or mesh returns 409 `mesh_preview_source_stale`; a successful binary
response echoes `X-Mesh-Snapshot-Key`. Keyless older clients retain their
previous behavior. This inexpensive path/size/mtime key is **not** a content
hash or a mesh receipt.

**Response Content-Type:** `application/octet-stream`

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/mesh-preview/binary -o mesh_preview.bin
```

---

### `GET /api/mesh-preview/render-source` and `POST /api/mesh-preview/render-grant`

**Auth:** Admin or Viewer

The mesh-preview counterparts of the post-processing render routes: `render-source?meshSnapshotKey=<64 hex>` verifies the preview mesh (409 `mesh_preview_source_stale` if it changed), and `render-grant` signs it for the Omniverse bridge.

---

### `GET /api/mesh-preview/debug`

**Auth:** Admin or Viewer

Diagnostic endpoint for debugging mesh face topology. Loads the mesh via PyVista, walks the face array, and reports polygon vertex counts and index validity for each boundary patch. Useful for diagnosing "Failed mesh checks" or rendering artifacts.

**Response (200):**

```json
{
  "patches": [
    {
      "name": "room_wall",
      "n_points": 5000,
      "n_faces": 9998,
      "max_idx": 4999,
      "vert_counts": {"3": 9998},
      "valid": true
    }
  ],
  "total": 12
}
```

A patch is `valid` if `max_idx < n_points` (no out-of-range vertex references).

**Example:**

```bash
curl -b cookie.txt http://localhost:$PORT/api/mesh-preview/debug
```

---

## 10. WebSocket Terminal

The server can expose an admin WebSocket terminal (PTY). It is **off by default**; start the server with `--enable-terminal` (the launcher passes it through). It listens on the WebSocket port: HTTP port + 1 through the launcher, `--ws-port` (default 8006) when `server.py` is run directly. This is not a REST endpoint; see [WebSocket Terminal](websocket.md).

**URL:** `ws://localhost:<ws-port>/?token=<session-token>`

**Auth:** Session token from `/api/auth/login` passed as query parameter `?token=`. Only **admin** sessions may connect; there is no loopback exception (`--trust-loopback` applies to HTTP only). Failures close with code 4001.

**Protocol:**
- Text frames from client → written to PTY stdin (bash keystrokes)
- Text frames from server → PTY stdout/stderr (terminal output, UTF-8)
- Resize frame from client, starting exactly with `{"type":"resize"`: `{"type":"resize","rows":30,"cols":120}`

On connect, the terminal starts `bash` in the case directory with the server's environment.

**Example (wscat):**

```bash
wscat -c "ws://localhost:$WS_PORT/?token=$(cat session_token.txt)"
```

---

## Appendix: Endpoint Summary Table

One row per path, checked against the routing table in `server.py` on 2026-10-21. The three admin delete routes (`/api/admin/users/<username>`, `/api/admin/sessions/<prefix>`, `/api/admin/bans/<ip>`) also accept `POST` as a `DELETE` alias. "Any" means any logged-in user (admin or viewer).

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | Public | Login, set session cookie |
| GET | `/api/auth/check` | Public | Check session validity |
| POST | `/api/auth/logout` | Any | Invalidate session |
| GET | `/api/admin/users` | Admin | List users |
| POST | `/api/admin/users` | Admin | Create user |
| DELETE | `/api/admin/users/<username>` | Admin | Delete user |
| POST | `/api/admin/users/<username>/password` | Admin | Change password |
| POST | `/api/admin/users/<username>/role` | Admin | Change role |
| GET | `/api/admin/sessions` | Admin | List sessions |
| DELETE | `/api/admin/sessions/<prefix>` | Admin | Revoke session |
| GET | `/api/admin/bans` | Admin | List IP bans |
| DELETE | `/api/admin/bans/<ip>` | Admin | Unban IP |
| POST | `/api/admin/upgrade` | Admin | Git pull upgrade (no rebuild) |
| POST | `/api/admin/upgrade/force` | Admin | Force upgrade (stash + pull) |
| POST | `/api/admin/restart` | Admin | Restart server process |
| POST | `/api/set-case-dir` | Admin | Set case directory (process-wide) |
| POST | `/api/write-file` | Admin | Write file to server |
| POST | `/api/create-dir` | Admin | Create directory |
| GET | `/api/browse-dir` | Any | Browse server filesystem |
| GET | `/api/read-file` | Any | Read text file |
| GET | `/api/read-file-binary` | Any | Read binary file as base64 |
| POST | `/api/run-mesh` | Admin | Submit mesh/solve job (optional `hybridHeat`) |
| POST | `/api/run-check-mesh` | Admin | Run checkMesh |
| GET | `/api/check-mesh` | Any | Check mesh existence |
| POST | `/api/prepare-case` | Admin | Save files, return command |
| GET | `/api/server-patches` | Any | List inlet/outlet patches |
| GET | `/api/log-stream` | Any | SSE log streaming |
| GET | `/api/job-status` | Any | Poll job status |
| POST | `/api/abort-job` | Admin | Cancel running job |
| GET | `/api/scan-case-status` | Any | Scan mesh + solve status |
| POST | `/api/pp/init` | Any | Init post-processor, returns `revision` |
| GET | `/api/pp/mesh` | Any | Patch metadata (JSON), `X-PP-Revision` |
| GET | `/api/pp/mesh/binary` | Any | Mesh geometry (binary), `X-PP-Revision` |
| GET | `/api/pp/field/{name}` | Any | Field data (binary), `X-PP-Revision` |
| GET | `/api/pp/runs` | Any | List run directories |
| POST | `/api/pp/streamlines` | Admin | Compute streamlines, `X-PP-Revision` |
| POST | `/api/pp/slice` | Admin | Plane slice, `X-PP-Revision` |
| POST | `/api/pp/volume` | Admin | Volume resampling, `X-PP-Revision` |
| GET | `/api/pp/render-source` | Any | Verify loaded result for display, `X-PP-Revision` |
| POST | `/api/pp/render-grant` | Any | Sign display products for Omniverse, `X-PP-Revision` |
| GET | `/api/pp/ai/status` | Any | AI worker status |
| GET | `/api/pp/ai/demo-model-path` | Any | Demo AI checkpoint path |
| POST | `/api/pp/ai/start` | Admin | Load AI model |
| POST | `/api/pp/ai/infer` | Admin | Run AI inference |
| GET | `/api/probes-latest` | Any | Probe monitoring data |
| POST | `/api/harvest-sensor-results` | Admin | Extract sensor results |
| POST | `/api/liquid/runs` | Admin | Create liquid runtime session |
| POST | `/api/liquid/runs/{id}/{start,pause,resume,step,cancel,inputs,equilibrium}` | Admin | Session lifecycle, online inputs, independent steady state |
| GET | `/api/liquid/runs/{id}[/{frames,result,checkpoint,executed-input,heat-audit,saved}]` | Any | Session status and artifacts; `saved` reads a run on disk |
| GET | `/api/liquid/runs` | Any | Saved and loaded runs of the case |
| POST | `/api/liquid/runs/{id}/restore` | Admin | Load a saved run as a read-only session |
| GET | `/api/hybrid/heat-ledger` | Any | Liquid-to-air heat ledger of the case |
| POST | `/api/liquid/export-liquidrun` | Admin | Save `.liquidrun` to disk |
| POST | `/api/liquid/solve` | Admin | One-shot liquid solve (SSE; older path) |
| POST | `/api/liquid/transient` | Admin | **Retired**, 410 Gone — use `/api/liquid/runs` |
| GET | `/api/liquid/results` | Any | Latest liquid result |
| POST | `/api/run-coupled-sim` | Admin | **Retired**, 410 Gone — coupling goes through `/api/run-mesh` `hybridHeat` |
| POST | `/api/ov/result-dataset` | Admin | Signed Omniverse grant for an air run |
| POST | `/api/ov/liquid-result-grant` | Admin | Signed Omniverse grant for a liquid DAE run |
| GET | `/api/ov-status` | Any | Omniverse bridge readiness |
| POST | `/api/render-scene/export` | Admin | Persist a render scene frame |
| POST | `/api/browser/close` | Admin | Close the automation browser |
| POST | `/api/browser/exec-js` | Admin | Execute JavaScript |
| POST | `/api/browser/load-proj` | Admin | Load project file |
| POST | `/api/browser/click` | Admin | Simulate click |
| POST | `/api/browser/export-stl` | Admin | Export STL files |
| POST | `/api/browser/export-cfdrun` | Admin | Export .cfdrun manifest (alias `/api/browser/export-cfd`) |
| POST | `/api/browser/export-case` | Admin | Export full case |
| POST | `/api/browser/full-pipeline` | Admin | End-to-end pipeline |
| GET | `/api/browser/state` | Any | Get browser state |
| GET | `/api/browser/geometry-status` | Any | Geometry diagnostics |
| GET | `/api/browser/logs` | Any | Browser console logs |
| GET | `/api/browser/screenshot` | Any | Browser viewport screenshot |
| GET | `/api/server-info` | Any | Server capabilities |
| POST | `/api/screenshot` | Admin | Save UI screenshot |
| POST | `/api/mesh-preview/load` | Admin | Load mesh preview |
| GET | `/api/mesh-preview/binary` | Any | Preview geometry (binary) |
| GET | `/api/mesh-preview/render-source` | Any | Verify preview mesh for display |
| POST | `/api/mesh-preview/render-grant` | Any | Sign preview mesh for Omniverse |
| GET | `/api/mesh-preview/debug` | Any | Mesh topology diagnostics |
