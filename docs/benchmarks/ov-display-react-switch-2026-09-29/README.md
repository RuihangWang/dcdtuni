# OV 显示战役 Stage 1 回执 — start_server.sh 参数化 + 主 React↔OV 无缝切换(2026-09-29)

**结论:PASS。** 主 React 前端(非 cfd_demo UI)通过 `--ov` 一体化启动的 Kit 桥完成
**decoded video + 场景 ACK + 双向往返切换**,全程 console 0 错误,Kit 单卡(GPU0)。

## 启动(新参数首跑即过)

- 40901:`CONDA_EXE=… src/start_server.sh --app react --port 8390 --ov --ov-gpu 0 --case-dir case/Shinaide_Liquid`
  (CUDA_VISIBLE_DEVICES=""、OMP 2 线程;启动器 4.2 s 完成并行拉起,Kit 后台就绪)。
- `--ov-gpu 0`(用户裁决默认)→ `DC_CFD_GPU=0` → Kit 仅 GPU0:**nvidia-smi 该 PID 仅 1 条
  GPU 进程记录(uuid=GPU0)**,用户 FVGN 训练共享 GPU0 不冲突。
- banner 打印 OV 行 + SSH 转发提示;`DCDTUNI_OV_SIGNALING_PORT=8301` 注入后端,
  `/api/server-info` 报告端点,浏览器 `preferredOvEndpoint` 回落 `location.hostname`
  (隧道形态成立)。
- 修复生效:`start_demo.sh` CONDA_PREFIX unbound(AI 段 set -u)不再炸非交互启动;
  Kit PID 落 `/tmp/dc_cfd_demo.kit.pid`;信令端口参数化 `DC_CFD_SIGNAL_PORT`。
- Kit 二次启动 **RTX ready 45 s**(cpd/shader 缓存热;首启 ~125 s)。

## 浏览器验证(真实 Chromium,Mac 经隧道 8390/8391/8301)

1. 登录态延续(auth/check 200),加载 `Shinaide_dae_cpu.cfdproj`:32 模型/800 网格。
2. 渲染器 Three.js→**Omniverse**:OvViewport 挂载 → AppStreamer direct 连接 →
   video **1600×900 live**(首查 2202 帧解码)→ 快照分块传输(~3700 行 chunk 日志)→
   Kit 端 `/World/RenderScene/Geometry/p_*` 批量建立(schema 校验的 USD 授权)→
   **scene-ack,状态"正在同步工程场景…"清除,无错误**(首次同步约 85 s,含首跑 USD 生成)。
3. 像素探针:meanLum 146.7,nonBlack 98.4%(场景实际渲染,非黑屏)。`evidence/ov-10`。
4. **往返回路**:切回 Three.js → OV 视口干净卸载、Three 画布恢复、**32 模型无损**、
   无错误消息(returnToThree 的相机/选择回读路径执行);再切 Omniverse → 二次连接
   视频恢复(2364→4995 帧解码)、**二次 scene-ack**(缓存后同步约 90 s,chunk 重传属
   设计内)。`evidence/ov-11`。
5. console 全程 **0 errors**(190 条均为 [dcdtuni:ov] 诊断 info);丢帧率 ~10-20%
   (隧道+软解,与 Stage 0 一致,不影响验收)。

## 观察项(移交 Stage 2)

- Kit hydra 对曲线类 primitive 报 `widths primvar is not valid`(大量 Warning,
  渲染未受影响)——Stage 2 几何通道时核对 snapshot 的曲线宽度 primvar 生成。
- 场景同步 ~85-90 s(800 网格 + 纹理,chunk 64 KiB/512 分块协议)——功能正确但偏慢;
  Stage 2 评估增量/缓存优化(usdgen/cpd 缓存已使二次 Kit 启动加速,场景层缓存待查)。
- `loadOverlay`/`loadResultLayers` 通道本轮未触发(无网格预览/结果层上下文)——
  Stage 2/4 分别验证。

## 边界

Kit 显示的是工程几何的**派生视觉表示**(render-scene schema 契约),非求解结果;
本轮无物理结论。现场保留:后端 8390/WS 8391、Kit 8301(PID 1109665)、Mac 隧道,
供 Stage 2 续用;8360/8361(PID 3282138)全程未触碰。
