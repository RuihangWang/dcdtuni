import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

// Generic server-side file picker, modeled on the STLGen frontend's file dialog:
// a navigable folder list + extension-filtered files, an up button, a selected-
// path display, and Cancel/Open. It talks to the Vite /api/browse-dir middleware
// (which lists folders + files matching the requested extensions under the repo
// root). Used for BOTH the scene-definition JSON ("Load CFD Geometry") and the
// CFD result dataset ("Load dataset"). On Open it hands the chosen absolute path
// back to the caller, which fires the matching backend command.

type Entry = { name: string; size?: number; mtime?: number };
type Listing = {
  cwd: string;
  parent: string | null;
  root: string;
  dirs: Entry[];
  files: Entry[];
};

function fmtSize(n?: number): string {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
function fmtDate(ms?: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function FileDialog({
  title,
  exts,
  startDir,
  hint,
  dirsOnly = false,
  onCancel,
  onPick,
}: {
  title: string;
  exts: string[];       // extension filter, e.g. [".json"] or [".vtu", ".case"]
  startDir: string;     // directory to open at
  hint?: string;        // optional one-line note under the title
  // Pick a FOLDER instead of a file. Needed for the AI model bundle, which is a
  // directory (source + checkpoint + dataset), not a single file. Without this the
  // dialog can only navigate into folders, never select one — Open requires a file,
  // and passing exts=[] does NOT hide files (the middleware falls back to every
  // dataset extension when the filter is empty).
  dirsOnly?: boolean;
  onCancel: () => void;
  onPick: (path: string) => void;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const browse = useCallback(
    async (dir?: string) => {
      setLoading(true);
      setError(null);
      setSelected(null);
      try {
        const params = new URLSearchParams();
        if (dir) params.set("path", dir);
        params.set("exts", exts.join(","));
        const r = await fetch(`/api/browse-dir?${params.toString()}`);
        const j = await r.json();
        if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
        setListing(j as Listing);
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [exts],
  );

  useEffect(() => { browse(startDir); }, [browse, startDir]);

  const join = (name: string) =>
    listing ? `${listing.cwd.replace(/\/$/, "")}/${name}` : name;

  return createPortal(
    <div className="ds-dlg-backdrop" onMouseDown={onCancel}>
      <div className="ds-dlg" onMouseDown={(e) => e.stopPropagation()}>
        <div className="ds-dlg-head">
          <span className="ds-dlg-title">{title}</span>
          <button className="ds-dlg-x" onClick={onCancel} title="Close">×</button>
        </div>
        {hint && <div className="ds-dlg-hint">{hint}</div>}

        <div className="ds-dlg-bar">
          <button
            className="ds-dlg-up"
            disabled={!listing?.parent}
            onClick={() => listing?.parent && browse(listing.parent)}
            title="Up one folder"
          >↑ Up</button>
          {/* Typed path. Clicking down to a case folder is a dozen clicks through
              absolute paths that are already known — pasting one is the fast route,
              and it is also what makes the flow scriptable for the head-browser
              drivers. Enter on a FILE picks it directly; on a directory it browses. */}
          <input
            className="ds-dlg-path"
            placeholder={listing?.cwd ?? "paste a path…"}
            defaultValue=""
            spellCheck={false}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              const v = (e.target as HTMLInputElement).value.trim();
              if (!v) return;
              if (!dirsOnly && exts.some((x) => v.toLowerCase().endsWith(x))) {
                onPick(v);
              } else {
                browse(v);
              }
            }}
          />
        </div>
        <div className="ds-dlg-cwd" title={listing?.cwd}>{listing?.cwd ?? "…"}</div>

        <div className="ds-dlg-list">
          {loading && <div className="ds-dlg-empty">Loading…</div>}
          {error && <div className="ds-dlg-err">Cannot list folder: {error}</div>}
          {!loading && !error && listing && (
            <>
              {listing.dirs.length === 0 && (dirsOnly || listing.files.length === 0) && (
                <div className="ds-dlg-empty">
                  {dirsOnly ? "No sub-folders here." : "No folders or matching files here."}
                </div>
              )}
              {listing.dirs.map((d) => (
                <div
                  key={"d:" + d.name}
                  className="ds-dlg-row ds-dlg-dir"
                  onClick={() => browse(join(d.name))}
                  title="Open folder"
                >
                  <span className="ds-dlg-ic">📁</span>
                  <span className="ds-dlg-name">{d.name}</span>
                </div>
              ))}
              {!dirsOnly && listing.files.map((f) => {
                const full = join(f.name);
                const sel = selected === full;
                return (
                  <div
                    key={"f:" + f.name}
                    className={sel ? "ds-dlg-row ds-dlg-file sel" : "ds-dlg-row ds-dlg-file"}
                    onClick={() => setSelected(full)}
                    onDoubleClick={() => onPick(full)}
                    title={full}
                  >
                    <span className="ds-dlg-ic">📄</span>
                    <span className="ds-dlg-name">{f.name}</span>
                    <span className="ds-dlg-meta">{fmtSize(f.size)}</span>
                    <span className="ds-dlg-meta">{fmtDate(f.mtime)}</span>
                  </div>
                );
              })}
            </>
          )}
        </div>

        <div className="ds-dlg-sel">
          <span className="ds-dlg-sel-label">Selected:</span>
          <span className="ds-dlg-sel-path">
            {dirsOnly
              ? (listing?.cwd ?? "—")
              : (selected ? selected.split("/").pop() : "—")}
          </span>
        </div>

        <div className="ds-dlg-foot">
          <button className="btn" onClick={onCancel}>Cancel</button>
          {dirsOnly ? (
            <button
              className="btn primary"
              disabled={!listing?.cwd}
              onClick={() => listing?.cwd && onPick(listing.cwd)}
            >Use this folder</button>
          ) : (
            <button
              className="btn primary"
              disabled={!selected}
              onClick={() => selected && onPick(selected)}
            >Open</button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
