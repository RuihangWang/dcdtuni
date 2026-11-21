# WebSocket Terminal

## Overview

The server can expose an interactive bash terminal over WebSocket. It runs in a POSIX PTY, so readline, colours and interactive programs such as `top` or `vim` work. It is meant for watching solver logs, running OpenFOAM utilities (`checkMesh`, `foamToVTK`) and general commands without leaving the browser.

**The terminal is off by default** (since the 2026-10-08 security hardening). Start the server with `--enable-terminal` to turn it on; the launcher passes unknown options through to `server.py`:

```bash
./src/start_server.sh --enable-terminal              # UI/API on 8000, terminal on 8001
./src/start_server.sh --port 9000 --enable-terminal  # terminal on 9001
```

The terminal listens on the WebSocket port: the HTTP port + 1 through the launcher (or `--ws-port`), 8006 when `server.py` is run directly. It binds to the same host as the backend (127.0.0.1 unless `--host` is given). When the terminal is off, the editor's terminal panel reports it as unavailable; mesh, solve and everything else go over HTTP `/api/*` and are not affected.

## Connection

```text
ws://localhost:<ws-port>/?token=<session_token>
```

The session token is the `token` field of the `POST /api/auth/login` response. Over SSH, forward the WebSocket port as well as the HTTP port.

## Authentication

Every connection needs a valid session token of an **administrator**. There is no exception for loopback clients: `--trust-loopback` affects HTTP only and never grants terminal access.

| Situation | Result |
|-----------|--------|
| valid admin token | shell starts |
| missing, invalid or non-admin token | closed with code **4001**, reason `Administrator authentication required` |
| server started without authentication configured | closed with code 4001, reason `Authentication is not configured` (cannot happen through the launcher) |

Connect and disconnect events are logged (`TERMINAL_CONNECT` / `TERMINAL_DISCONNECT`) to the auth log, `<auth-dir>/server_auth.log`.

## Protocol

### Incoming messages (client → server)

The server accepts two message formats:

**Plain text / keystrokes** — any text frame that is not a resize message is written directly to the PTY master file descriptor. Newline (`\n`), arrow keys, Ctrl sequences, etc. are all forwarded verbatim to the bash subprocess.

**Resize control message** — a JSON string that starts exactly with `{"type":"resize"` (no space after the colon, as `JSON.stringify` writes it):

```json
{"type":"resize","rows":30,"cols":120}
```

On receipt, the server calls `TIOCSWINSZ` on the PTY master to resize the terminal window. Send this message whenever the user resizes their terminal pane so that programs like `vim` and line-wrapped output adapt correctly. The initial size is 30 rows × 120 columns.

Binary frames are also accepted and forwarded to the PTY as raw bytes.

### Outgoing messages (server → client)

The server continuously reads from the PTY master (non-blocking, 16 KiB chunks) and forwards everything as **UTF-8 text frames**. This includes:

- Shell prompts and echoed keystrokes
- stdout and stderr from all commands
- ANSI/VT100 escape sequences (colors, cursor movement)

Invalid UTF-8 bytes are replaced with the Unicode replacement character (U+FFFD).

## Auto-Activation

On connection, before exposing the shell prompt, the server:

1. Detects the conda environment name from the running Python interpreter path (`sys.executable` → split on `/envs/`)
2. Writes a temporary `rcfile` under `_tmp/terminal/` that sources `~/.bashrc`, then runs `conda activate <env_name>`, then `cd <case_dir>`
3. Starts `bash --rcfile <tmpfile> -i` with the server's environment (`TERM=xterm-256color`)

Every terminal session therefore starts in the configured case directory (or the shared frontend directory when no case directory is set) with the server's environment, whose `PATH` the launcher already put in front of the repository environment's `bin/`. With the repository environment (`.codex-tmp/envs/dcdtuni`) the name-based `conda activate dcdtuni` may not find it, but the inherited `PATH` still points at the same Python and OpenFOAM.

## Error Codes

| Close code | Meaning                                                                                              |
|------------|------------------------------------------------------------------------------------------------------|
| `4001`     | Authentication failure: no valid token provided, or the authenticated user lacks the `admin` role    |

Normal disconnection (e.g. the user closes the tab) produces standard close code `1000` or `1001`.

## Cleanup

When the WebSocket closes for any reason, the server:

1. Closes the PTY master file descriptor
2. Sends `SIGTERM` to the bash process group (`os.killpg`) to terminate all child processes
3. Logs `TERMINAL_DISCONNECT` to the auth log

## JavaScript Example

```javascript
// Obtain a token first via POST /api/auth/login; wsPort comes from GET /api/server-info
const token = "<your_session_token>";
const ws = new WebSocket(`ws://localhost:${wsPort}/?token=${token}`);

ws.onopen = () => {
    // Notify the server of the current terminal size
    ws.send(JSON.stringify({ type: "resize", rows: 24, cols: 80 }));
};

ws.onmessage = (event) => {
    // event.data is a UTF-8 string — pipe to a terminal emulator (e.g. xterm.js)
    term.write(event.data);
};

ws.onclose = (event) => {
    if (event.code === 4001) {
        console.error("Auth failed:", event.reason);
    }
};

// Send a keystroke
ws.send("ls -la\n");

// Respond to user resizing their terminal pane
window.addEventListener("resize", () => {
    ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
});
```

For a full-featured in-browser terminal, connect the WebSocket to an [xterm.js](https://xtermjs.org/) instance using `term.onData(data => ws.send(data))` for input and `ws.onmessage = e => term.write(e.data)` for output.
