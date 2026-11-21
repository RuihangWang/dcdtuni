# Shinaide React → Omniverse 实机切换验收

2026-09-27，40901 自有仓库 `/mnt/lv/home/litianyu/mycode/dcdtuni`。测试对象是 React 中打开的 Shinaide 工程（32 个模型、Three.js 统计 800 个 mesh）；OV Kit 从同一仓库的扩展启动。使用且只使用物理 GPU 7（`GPU-aba0999f-5d2d-81c1-c6fe-3d74d52671c2`），CPU 亲和 4–7、数值线程 2。会话结束后 Kit 和私有 Xvfb 正常退出，GPU 7 回到 6 MiB、0% 使用率且无计算进程。

此前未节流的传输中，浏览器 SDK 接受了 3612 个场景块，Kit 只收到 1230 个，且未收到最后的 `loadRenderScene`。本次改为每 64 块等待一次 Kit 的 `renderSceneProgress` 确认，最后一批也确认后才加载。真实 Kit 日志显示两次连续切换合计收到 2 个 `beginRenderScene`、7224 个 `renderSceneChunk`、114 个进度屏障和 2 个 `loadRenderScene`，无 bridge 渲染命令错误。

第一次切到 OV 后，浏览器视频 `readyState=4`、同步提示消失，画面出现 Shinaide 设备与管网；切回 Three.js 后 OV 容器卸载，原有 800 个 mesh 保留。第二次切到 OV 同样得到完整场景与视频，见[第二次切换截图](second-switch.png)，随后再次切回 Three.js。CPU 协议测试中，React `ovProtocol.test.ts` 39 项通过，Kit 场景/进度测试 11 项通过；React 全套 2922 项通过，TypeScript 检查和生产构建通过。

验收范围是当前工程几何、相机与选中状态的流送，不代表风冷网格或 CFD 求解已通过。真实 Shinaide 的严格 `checkMesh` 仍有 6 项失败，OV 的后处理场数据与 Three.js 完全一致性也未在本次验证。完整运行日志和缓存留在仓库内被忽略的 `.codex-tmp/ov-live-gpu7-20260927-04/`，此处只入库紧凑回执与截图；原始 Kit 日志含传输消息正文，不宜直接提交。

## 常驻 Kit 与生产页面补验

限时验收结束后，React 页面需要一项单卡 Kit 后端才能随时切到 OV。新 `--service` 模式在 40901 使用同一 GPU 7 与私有 Xvfb 持续运行，`DC_CFD_AI=0`，停止仍通过自己的 `stop.request`。实验发现把 Kit **全局**输出级别设为 warning 会让 8301 监听却无法完成浏览器连接；保留全局 info、仅将 `omni.kit.livestream.webrtc.plugin` 的日志级别降到 warning 后，生产页面恢复连接，传输块正文未写入 Kit stdout/internal log。

在生产页面 `http://127.0.0.1:18360/` 加载仓库中的 `Shinaide_dae_cpu.cfdproj`（32 个模型、800 mesh），OV 视频达到 `readyState=4`，场景同步提示消失并呈现设备与管网，见[生产页截图](persistent-production.png)；之后切回 Three.js，常驻 Kit 保持运行，测试浏览器断开以释放单客户端流。服务目录为仓库内 `.codex-tmp/ov-service-gpu7-20260927-02/`，当时状态 `running`，计算进程只在 GPU 7。冷连接需要同时配置远端的 OV 信令主机；新增 `/api/server-info` 字段可让通过本机 HTTP 隧道访问的 React 页面自动获得该地址，替代误用 `127.0.0.1`。

推送并以 `DCDTUNI_OV_SIGNALING_HOST=40901.dynip.ntu.edu.sg` 重启 8360 后端后，重新加载 `127.0.0.1:18360` 页面，**没有手动填写 OV 主机**：连接框自动显示 `40901.dynip.ntu.edu.sg:8301`。再次加载同一 800-mesh 工程，直接切到 OV，视频 `readyState=4`、场景最终 ACK 完成；切回 Three.js 后 Kit 仍为 `running`。长期日志无 `Processing custom kit message` 场景块正文，GPU 7 为唯一 Kit 计算设备。
