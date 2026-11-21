# Upgrade Button Design Spec

**Date**: 2026-04-03  
**Goal**: Add a one-click "Upgrade" button in the Admin panel that pulls latest code from git and restarts the server.

## Flow

```
User clicks "Upgrade" in Admin panel
  → POST /api/admin/upgrade
  → git fetch + git pull --recurse-submodules in dcdtuni root
  → Success?
     ├─ Yes → return success, frontend calls /api/admin/restart, polls until server back, auto-reload
     ├─ Already up to date → show message, no restart
     └─ Conflict → return conflict file list, frontend shows dialog
        → User clicks "Continue"?
           ├─ Yes → POST /api/admin/upgrade/force (stash + pull), then restart
           └─ No → cancel
```

## API Endpoints

### POST /api/admin/upgrade
- Admin-only
- Runs in dcdtuni root: `git fetch origin`, compare HEADs, `git pull --recurse-submodules`
- On conflict: `git merge --abort`, return file list
- Response: `{ok, status: "updated"|"already_up_to_date"|"conflict", files?, log[]}`

### POST /api/admin/upgrade/force
- Admin-only
- Main repo: `git stash save "upgrade-YYYY-MM-DD-HH-mm-ss"`
- Submodule: `cd STLGeneratorForOpenFoam && git stash save ...` (if dirty)
- Then: `git pull --recurse-submodules`
- Response: `{ok, status: "updated", stashed: true, log[]}`

### POST /api/admin/restart
- Admin-only
- Sends response first, then: `subprocess.Popen("sleep 2 && exec python server.py ...", shell=True)` + `sys.exit(0)`
- Response: `{ok: true}`

## Frontend

- "Upgrade" button in Admin settings panel
- On click: show modal with log output area
- Log area displays git command output in real-time style
- Conflict dialog: shows file list + "Continue"/"Cancel" buttons
- After successful upgrade: show "Restarting server..." spinner, poll `GET /api/server-info` every 2s, auto `location.reload()` on success

## Server Restart Mechanism

- `subprocess.Popen` spawns new server process with `sleep 2` delay
- Old process calls `sys.exit(0)` after sending response
- 2s gap allows port to be released before new process binds

## Decisions

- Button location: Admin settings panel (admin-only)
- Restart: spawn + exit (not os.execv) for safety
- Stash: both main repo and submodule
- Submodule update: via `--recurse-submodules` from root
