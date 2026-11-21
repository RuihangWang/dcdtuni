import { AppStreamer } from "@nvidia/omniverse-webrtc-streaming-library";

export type StreamStatus = "waiting" | "connecting" | "connected" | "error";

let customHandler: (msg: unknown) => void = () => {};
export function setCustomEventHandler(fn: (msg: unknown) => void) {
  customHandler = fn;
}

export async function connectStream(opts: {
  videoElementId: string;
  audioElementId: string;
  onStatus: (s: StreamStatus) => void;
}) {
  const params = new URLSearchParams(window.location.search);
  const signalingServer =
    params.get("server") ||
    (import.meta as any).env.VITE_OMNIVERSE_SERVER ||
    window.location.hostname;
  const signalingPort = Number(
    params.get("signalingPort") ||
      (import.meta as any).env.VITE_SIGNALING_PORT ||
      "8301"
  );

  const streamConfig = {
    videoElementId: opts.videoElementId,
    audioElementId: opts.audioElementId,
    signalingServer,
    signalingPort,
    width: 1920,
    height: 1080,
    fps: 60,
    authenticate: false,
    nativeTouchEvents: true, // forwards pointer events -> ROI gizmo dragging
    maxReconnects: 20,
    onStart: () => opts.onStatus("connected"),
    onUpdate: () => {},
    onStop: () => opts.onStatus("error"),
    onTerminate: () => opts.onStatus("error"),
    onCustomEvent: (msg: unknown) => customHandler(msg),
  };

  opts.onStatus("connecting");
  // streamSource "direct" matches kit-cae's proven stream-client.html (no signaling broker).
  await AppStreamer.connect({ streamSource: "direct", streamConfig } as any);
}
