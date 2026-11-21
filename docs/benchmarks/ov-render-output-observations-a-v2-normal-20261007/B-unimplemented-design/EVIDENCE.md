# Exact installed source evidence

All paths/hashes are source-only, not active loaded-module claims. No API was invoked.
| Entry | Exact source lines | Fact |
|---|---|---|
| widget | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.widget.viewport-e33603573147cc56/omni/kit/widget/viewport/widget.py` 51–69,191–221,501–517,549–597,614–643 | public stored API/visible/delegate; renderer-to-UI sync; fill-frame/DPI path |
| delegate | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.widget.viewport-e33603573147cc56/omni/kit/widget/viewport/display_delegate.py` 15–74 | public create gets real ui_frame; update forwards opaque texture; only size/provider exposed |
| api | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.widget.viewport-e33603573147cc56/omni/kit/widget/viewport/api.py` 306–326,377–414,428–445,465–501,521–553 | last delivered metadata vs UI projection; image/canvas conformance; texture UV mapping |
| texture | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.widget.viewport-e33603573147cc56/omni/kit/widget/viewport/impl/texture.py` 359–384 | Hydra result stores metadata then sends texture and presentation key to UI; no encoder frame association |
| ui | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.ui-0e529a493c7b9bf0/omni/ui/_ui.pyi` 3144–3154,4799–4824,4826–4848,4957–4968,5526–5535,5555–5558,11004–11007,11069–11099,11184–11199,11376–11385,11486–11504,11550–11554,11623–11642,11990–12003 | public Window.app_window; own Frame/Image policy/position/size getters; units distinction |
| appwindow | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.appwindow-35766b062bc43ad5/omni/appwindow/_appwindow.pyi` 79–92,136–162 | position/size/raw scales getters |
| renderer | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.renderer.core-b0c65c993703d4a5/omni/kit/renderer/bind/_renderer.pyi` 25–59,102–118 | framebuffer dimensions/attachment and separate render events |
| capture | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.renderer.capture-cddfe3dbd89512ae/omni/kit/renderer_capture/_renderer_capture.pyi` 93–104,169–183,232–243 | callback has dimensions but no shared frame ID; render-product method unsupported; source only, never called |
| stream-app-doc | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.app-9.0.0+109.0.0.lx64.r.cp312/docs/Overview.md` 3–26,128–153 | entire framebuffer architecture/capture/event/resize sequence |
| stream-app-config | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.app-9.0.0+109.0.0.lx64.r.cp312/config/extension.toml` 1–6,17–32,41–50 | app9 native plugin/no Python implementation |
| stream-app-init | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.app-9.0.0+109.0.0.lx64.r.cp312/omni/kit/livestream/app/__init__.py` 9–10 | empty placeholder |
| stream-core-doc | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.core-9.0.0+109.0.0.lx64.r.cp312/docs/Overview.md` 9–16,28–45,63–71 | factory/IServer architecture; not Python frame receipt |
| stream-core-config | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.core-9.0.0+109.0.0.lx64.r.cp312/config/extension.toml` 1–8,22–34 | core9 native plugin |
| stream-core-init | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.core-9.0.0+109.0.0.lx64.r.cp312/omni/kit/livestream/core/__init__.py` 9–10 | empty placeholder |
| webrtc-doc | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.webrtc-9.0.2+109.0.0.lx64.r.cp312/docs/Overview.md` 3–12,36–66,92–105 | CUDA buffer width/height feed and dynamic resize; no common Hydra frame ID |
| webrtc-config | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.webrtc-9.0.2+109.0.0.lx64.r.cp312/config/extension.toml` 1–8,20–36 | webrtc9.0.2 native plugin |
| webrtc-init | `/mnt/lv/home/litianyu/.local/share/ov/data/exts/v2/omni.kit.livestream.webrtc-9.0.2+109.0.0.lx64.r.cp312/omni/kit/livestream/webrtc/__init__.py` 9–10 | empty placeholder |

Full SHA and exact numbered excerpts are in dependencies.json/source-excerpts. Correct renderer cohort is b0c1.1.1 and capture cddfe1.0.2 from actual build links, not the older c7591.2.1 design-copy mistake. The original approved A design and F2 implementation are only referenced, never copied or modified.
