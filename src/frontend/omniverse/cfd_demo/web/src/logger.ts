// Global "super-logger" for the Omniverse CFD demo.
//
// One ring buffer that every part of the UI writes to, and the Operation Log panel
// subscribes to. Two sources feed it:
//   1. UI operations  — sendCommand() logs every command sent to Kit (src "ui").
//   2. Kit / Omniverse — the LogPanel pipes the Kit process stdout+stderr here
//      (src "kit") from the /api/kit-log SSE stream (print/log/error/warning).
//
// Keep this dependency-free (no React) so any module can import and log.

export type LogLevel = "cmd" | "info" | "warn" | "error" | "debug";

export interface LogEntry {
  id: number;
  t: number; // epoch ms
  level: LogLevel;
  src: string; // "ui" | "kit" | ...
  msg: string;
}

const MAX = 4000; // ring-buffer cap (older lines drop off the front)
let _id = 0;
const buffer: LogEntry[] = [];
const listeners = new Set<(entries: LogEntry[]) => void>();

function emit() {
  const snapshot = buffer.slice();
  listeners.forEach((l) => l(snapshot));
}

export function pushLog(level: LogLevel, src: string, msg: string) {
  buffer.push({ id: ++_id, t: Date.now(), level, src, msg });
  if (buffer.length > MAX) buffer.splice(0, buffer.length - MAX);
  emit();
}

export const logCmd = (msg: string) => pushLog("cmd", "ui", msg);
export const logInfo = (src: string, msg: string) => pushLog("info", src, msg);
export const logWarn = (src: string, msg: string) => pushLog("warn", src, msg);
export const logError = (src: string, msg: string) => pushLog("error", src, msg);

/** Subscribe to the log; the listener is called immediately with the current
 *  buffer and again on every new entry. Returns an unsubscribe function. */
export function subscribeLog(listener: (entries: LogEntry[]) => void): () => void {
  listeners.add(listener);
  listener(buffer.slice());
  return () => {
    listeners.delete(listener);
  };
}

export function clearLog() {
  buffer.length = 0;
  emit();
}

/** Resolve when a NEW log entry (appended after this call) matches `predicate`.
 *  Used to detect one-way backend receipts (e.g. "load_dataset OK") since the
 *  WebRTC command channel has no completion signal. Rejects after `timeoutMs`. */
export function waitForLog(
  predicate: (e: LogEntry) => boolean,
  timeoutMs = 120000,
): Promise<LogEntry> {
  const sinceId = _id; // only consider entries created after this point
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (fn: () => void) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      unsub();
      fn();
    };
    const unsub = subscribeLog((entries) => {
      const hit = entries.find((e) => e.id > sinceId && predicate(e));
      if (hit) finish(() => resolve(hit));
    });
    const timer = setTimeout(
      () => finish(() => reject(new Error("waitForLog timeout"))),
      timeoutMs,
    );
  });
}
