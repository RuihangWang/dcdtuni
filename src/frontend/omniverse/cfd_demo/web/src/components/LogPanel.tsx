import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  clearLog,
  pushLog,
  subscribeLog,
  type LogEntry,
  type LogLevel,
} from "../logger";

// Operational Log panel — the bottom dock that mirrors EVERY UI command (logger
// src "ui") AND the live Kit / Omniverse process output (src "kit") streamed from
// the Vite /api/kit-log SSE endpoint. Modeled on the STLGen frontend's Operation
// Log: full-width bottom strip, resizable by dragging its top edge, collapsible,
// auto-scrolling, with a clear button. This is the primary debug surface.

const MIN_H = 90;
const MAX_FRAC = 0.7; // never taller than 70% of the viewport
const DEFAULT_H = 200;

// Classify a raw Kit log line into a level by the tags Omniverse/carb emit.
// Kit lines look like: "[12.345s] [Error] [omni.foo] message".
function classifyKit(line: string): LogLevel {
  const l = line.toLowerCase();
  if (/\[(error|fatal)\]/.test(l) || /\btraceback\b/.test(l) || /\berror:/.test(l))
    return "error";
  if (/\[warn(ing)?\]/.test(l) || /\bwarning:/.test(l)) return "warn";
  // dc.cfd_bridge prints command receipts with a recognisable prefix.
  if (/\[dc\.cfd_bridge\]|handle_\w+|command=/.test(l)) return "info";
  return "debug";
}

const LEVEL_LABEL: Record<LogLevel, string> = {
  cmd: "CMD",
  info: "INFO",
  warn: "WARN",
  error: "ERR",
  debug: "··",
};

function fmtTime(t: number): string {
  const d = new Date(t);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(
    d.getMilliseconds(),
    3,
  )}`;
}

export function LogPanel() {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [height, setHeight] = useState(DEFAULT_H);
  const [autoScroll, setAutoScroll] = useState(true);
  // Per-level filter toggles. All on by default.
  const [filters, setFilters] = useState<Record<LogLevel, boolean>>({
    cmd: true,
    info: true,
    warn: true,
    error: true,
    debug: true,
  });
  const listRef = useRef<HTMLDivElement>(null);

  // 1) Subscribe to the global logger (UI commands + anything pushLog'd).
  useEffect(() => subscribeLog(setEntries), []);

  // 2) Pipe the Kit process stdout/stderr (SSE) into the same logger as src "kit".
  // Publish the dock's height so the side panels can stop above it. The dock is
  // position:fixed with a user-draggable height, so a hardcoded reserve in CSS was
  // wrong the moment anyone resized it — the AI panel's lower half disappeared behind
  // the log. Writing the live value to a custom property keeps them in sync.
  useEffect(() => {
    const px = collapsed ? 34 : height + 4;
    document.documentElement.style.setProperty("--logdock", `${px}px`);
  }, [collapsed, height]);

  useEffect(() => {
    let es: EventSource | null = null;
    let stopped = false;
    const connect = () => {
      if (stopped) return;
      es = new EventSource("/api/kit-log");
      es.onmessage = (ev) => {
        let line = ev.data;
        try {
          line = JSON.parse(ev.data);
        } catch {
          /* already a plain string */
        }
        if (typeof line === "string" && line.trim().length)
          pushLog(classifyKit(line), "kit", line);
      };
      es.onerror = () => {
        // EventSource auto-reconnects, but if the server closed we recreate it.
        if (es && es.readyState === EventSource.CLOSED && !stopped) {
          es.close();
          setTimeout(connect, 2000);
        }
      };
    };
    connect();
    return () => {
      stopped = true;
      es?.close();
    };
  }, []);

  // 3) Auto-scroll to the newest line unless the user scrolled up.
  useLayoutEffect(() => {
    if (autoScroll && listRef.current && !collapsed) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [entries, autoScroll, collapsed]);

  const onScroll = () => {
    const el = listRef.current;
    if (!el) return;
    // Re-engage auto-scroll only when the user is within ~24px of the bottom.
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    setAutoScroll(atBottom);
  };

  // Drag the top edge to resize. Pointer math is inverted: dragging UP grows it.
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const sy = e.clientY;
    const sh = height;
    const maxH = window.innerHeight * MAX_FRAC;
    const onMove = (ev: MouseEvent) => {
      const next = Math.min(maxH, Math.max(MIN_H, sh + (sy - ev.clientY)));
      setHeight(next);
      if (collapsed) setCollapsed(false);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const shown = entries.filter((e) => filters[e.level]);
  const counts = { warn: 0, error: 0 };
  for (const e of entries) {
    if (e.level === "warn") counts.warn++;
    else if (e.level === "error") counts.error++;
  }

  const toggleFilter = (lv: LogLevel) =>
    setFilters((f) => ({ ...f, [lv]: !f[lv] }));

  return (
    <div className={collapsed ? "logpanel collapsed" : "logpanel"}
         style={collapsed ? undefined : { height }}>
      {!collapsed && <div className="logpanel-resize" onMouseDown={startResize} />}
      <div className="logpanel-bar">
        <button className="logpanel-collapse" onClick={() => setCollapsed((c) => !c)}
                title={collapsed ? "Expand log" : "Collapse log"}>
          {collapsed ? "▲" : "▼"}
        </button>
        <span className="logpanel-title">Operational Log</span>
        <span className="logpanel-stat">{shown.length}/{entries.length}</span>
        {counts.error > 0 && <span className="logpanel-badge err">{counts.error} err</span>}
        {counts.warn > 0 && <span className="logpanel-badge warn">{counts.warn} warn</span>}
        <div className="logpanel-filters">
          {(["cmd", "info", "warn", "error", "debug"] as LogLevel[]).map((lv) => (
            <button key={lv}
                    className={filters[lv] ? `lf lf-${lv} on` : `lf lf-${lv}`}
                    onClick={() => toggleFilter(lv)}
                    title={`Toggle ${lv} lines`}>
              {LEVEL_LABEL[lv]}
            </button>
          ))}
        </div>
        <span className="logpanel-spacer" />
        {!autoScroll && !collapsed && (
          <button className="logpanel-btn" onClick={() => setAutoScroll(true)}
                  title="Jump to newest">↓ live</button>
        )}
        <button className="logpanel-btn" onClick={clearLog} title="Clear log">Clear</button>
      </div>
      {!collapsed && (
        <div className="logpanel-body" ref={listRef} onScroll={onScroll}>
          {shown.length === 0 ? (
            <div className="logpanel-empty">No log entries.</div>
          ) : (
            shown.map((e) => (
              <div key={e.id} className={`logline lvl-${e.level}`}>
                <span className="ll-time">{fmtTime(e.t)}</span>
                <span className={`ll-src src-${e.src}`}>{e.src}</span>
                <span className="ll-lvl">{LEVEL_LABEL[e.level]}</span>
                <span className="ll-msg">{e.msg}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
