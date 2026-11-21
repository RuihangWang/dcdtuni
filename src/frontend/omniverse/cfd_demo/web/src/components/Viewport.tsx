import { useEffect, useRef, useState } from "react";
import { connectStream, StreamStatus } from "../stream";
import { resetScene } from "../streamMessages";

export function Viewport() {
  const [status, setStatus] = useState<StreamStatus>("waiting");
  const started = useRef(false);
  const resetSent = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    connectStream({
      videoElementId: "remote-video",
      audioElementId: "remote-audio",
      onStatus: (s) => {
        setStatus(s);
        // On the FIRST connection of this page load, reset the long-lived Kit viewport
        // to a clean slate so a browser refresh is a from-scratch demo. Guarded so a
        // mid-session WebRTC reconnect (maxReconnects) does NOT wipe the user's work.
        // Small delay lets the command data channel settle after onStart.
        if (s === "connected" && !resetSent.current) {
          resetSent.current = true;
          window.setTimeout(() => resetScene(), 1000);
        }
      },
    }).catch((e) => {
      console.error(e);
      setStatus("error");
    });
  }, []);

  return (
    <div className="viewport">
      <video id="remote-video" autoPlay playsInline muted tabIndex={-1} />
      <audio id="remote-audio" autoPlay />
      {status !== "connected" && (
        <div className={`stream-status ${status}`}>{status}…</div>
      )}
    </div>
  );
}
