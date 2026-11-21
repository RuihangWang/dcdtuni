# OV 显示战役 Stage 3 设计 — 表面切片 + 流线交互(2026-09-29,待实施)

输入:Stage 0/2 实测(scene.py 架构)、用户裁决(唯二 OV 视口交互=流线撒点球+切面预览圈)、
cfd_demo 参考实现的既有模式。**本设计不含建模交互;OV 仅显示。**

## 1. 现状架构要点(scene.py / handlers.py 实测)

- 体渲染:kit-cae `omni.cae.viz` 在**创建时一次性体素化**;换场/开关 ROI 裁剪都触发
  整卷重建(`_do_rebuild_volume`),裁剪区读 ROI box 的 local bound(恒等变换 AABB)。
- ROI:盒 gizmo 可视口拖拽,`syncFromViewport` 把拖拽结果回读为前端数值(仲裁先例)。
- 流线:`generate_streamlines` 在 vec3 U 上积分出 BasisCurves,按标量着色;
  种子球 `_resolve_seed_center`:**前端数值 vs 视口拖拽,拖拽优先**(ui_changed 判定)。
- **无任何 slice 实现**;`extract_roi` 是"子区域导出 vtu 文件",不是可视切片。

## 2. 表面切片:主方案 = VTK cutter 重采样 → 点彩 USD 面片

- 命令 `setSlice {visible, origin[3], normal[3], radius, field:'T'|'Umag', domain:[lo,hi]|null}`
  (domain null = 用 dataset ranges 自动);桥内 vtk 线程池执行
  `vtkCutter(plane) → 三角化 → 点标量转 displayColor`,产物为
  `/World/CAE/Slice_<n>` 的 Mesh + vertex displayColor,**独立于体渲染**(不触发整卷重建)。
- 缓存键 `(dataset_sha, plane 量化参数, field, domain)`;参数拖动节流 200 ms 重建,
  拖拽期间只移动**预览圈**(零成本),松手/节流点才真切。
- **切面预览圈**(唯二交互之二):视口内一个圆盘 gizmo(中心=origin,法向=normal,
  半径=radius 的视觉柄),拖拽走与种子球同一仲裁模式(`_resolve_slice_plane`:
  视口拖拽优先,面板数值兜底);圈仅预览,不代表已重建的切片。
- 备选(降级)方案:ROI 盒压成薄片当 slab 切片——复用现有裁剪机器,但继承 ROI 已知
  bug 与整卷重建代价,只作 fallback 演示,不作主路径。

## 3. 流线:复用现有,补主 React 面板

- 命令面已有 `setStreamlines`/`setSeedSphere`/`syncFromViewport`;主 React 新增 OV
  后处理面板区块:种子球中心/半径/种子数数值输入 + "视口拖拽优先"提示 + 流线开关/
  管径/着色场选择;全部走 ovProtocol 命令 + ACK(Stage 1 通道)。
- 撒点球拖拽 = 参考实现既有交互,直接保留(用户裁决的唯一交互之一)。

## 4. 主 React 侧 OV 后处理面板(新组件)

- 位置:渲染器=Omniverse 时在后处理页签显示 OV 区块(体渲染场/阈值、切片、流线三组);
  渲染器=Three.js 时隐藏(各自后处理独立,不互相污染状态)。
- 所有控件状态进 projectStore 的 run-config 派生(不写 .cfdproj);切换渲染器不丢
  (与 Stage 1 无缝切换纪律一致)。

## 5. 测试与验收

- CPU 契约 pytest(cfd_demo/tests 模式):setSlice 参数校验(零法向/非有限值拒绝、
  domain 逆序拒绝)、缓存键命中/失效、预览圈仲裁(拖拽优先)纯函数单测。
- Kit 授权测试(40901 pxr):切片面片 prim 的 displayColor vertex 插值、法向/半径落地。
- browser-use 门:风冷 case 上体渲染+切片+流线三件套逐项截图联动;切面圈与撒点球
  视口拖拽→重渲染闭环;液冷 case 按 Stage 0 裁决(几何温度着色,无体渲染)不要求切片。
- 回归:cfd_demo 129 + vitest 全量 + build;每步合 main 纪律不变。

## 6. 二分实验 runbook 增补(renderAck 回归, Stage 2 遗留)

旧扩展树已预置:`40901:/tmp/ov-bisect-ext/`(git archive 8cf4ca14 的 extensions,
`grep -c SetInterpolation render_scene.py = 0` 验证为旧码)。对照启动命令(与现网 Kit
同 kit-cae 构建、同环境,仅扩展树不同;信令 8302、GPU4 避冲突):

```bash
# 40901,仓库根;先 fuser -k 8302/tcp 确认空闲
cd ~/mycode/dcdtuni/src/backend/kit-cae/_build/linux-x86_64/release
CUDA_VISIBLE_DEVICES=4 DISPLAY=:99 DC_CFD_GPU=4 \
  ./omni.cae_streaming.kit.sh --no-window \
  --ext-folder /tmp/ov-bisect-ext --enable dc.cfd_bridge \
  --/exts/omni.kit.livestream.app/primaryStream/signalPort=8302 \
  --/renderer/activeGpu=0 --/renderer/multiGpu/enabled=false \
  --/persistent/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0 \
  --/persistent/exts/omni.cae.viz/defaultBoundingBoxDevice=cuda:0 \
  --/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0 \
  --/exts/omni.cae.viz/defaultBoundingBoxDevice=cuda:0 \
  --/exts/omni.warp.core/devices=cuda:0 \
  --/app/window/hideUi=false \
  --/persistent/app/viewport/Viewport/Viewport0/hud/visible=false \
  > /tmp/ov_bisect_old_ext.kit.log 2>&1 &
```
(掩码内 GPU4=cuda:0;activeGpu=0 为掩码后表索引,循 start_demo.sh 注释纪律。)
浏览器侧用 RendererControl 的手动端口输入改 8302 连对照实例:ack 恢复→代码差
(在 98b26846..8f1f44e4 四提交内再二分);仍断→环境差(转查驱动 ICD/GPU 共享态/
插件会话绑定),并对新扩展树实例抓 verbose 日志的 webrtc 出站段。
