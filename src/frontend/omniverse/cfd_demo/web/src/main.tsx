import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import * as commands from "./streamMessages";

// Expose the typed command senders on window for scripting/debugging the demo
// (e.g. window.cfd.setRoiBox([10,0,6],[21,5,13]) to drive the ROI from a console
// or a browser-automation harness). The React panel remains the primary UI.
(window as unknown as { cfd: typeof commands }).cfd = commands;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
