# CDU actual IAB focus and Shift interaction before closed-display build

Root used the Windows-local Codex IAB, connected to the 40901 integration service at 8396. Focus moved the CDU into the unobscured area. A mistaken click on the project-root row selected all 32 models; a subsequent empty-canvas click actually cleared selection. Actual Shift click on the service-door area selected only m_30 and showed the CDU inspector; the second Shift click cleared it. Camera/selection interactions left original project bytes unchanged.

The service door was visibly green but the cabinet side appeared gray. This records a visual gap rather than claiming whole-cabinet highlight acceptance. Closed-cabinet pipe clipping was committed but was not yet built into the served asset.

The two PNGs are WebGL canvas images saved by the actual product screenshot button. LogPanel.tsx uses __viewportRender and canvas.toDataURL; they are 1900x995 render outputs and do not capture React controls or the full 1520x956 IAB page. UI claims here come from actual DOM observations and CUA screenshots displayed during the session. No geometry, routing, opacity or input values were edited.
