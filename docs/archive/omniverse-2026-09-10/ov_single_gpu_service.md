# React 的单 GPU Omniverse 后端

React 生产页仍由 `src/start_server.sh --app react` 提供；OV 切换连接 40901 的 Kit 信令端口 8301，不需要第二套 Web 前端。Kit 后端使用 `tools/kit_acceptance_harness.py --service --execute` 单独监管：启动前按 UUID/PCI 核验空闲 GPU 和端口，独占一张卡，使用私有 Xvfb，禁用 AI worker，所有日志、缓存和纹理写进仓库 `.codex-tmp/`。服务持续到收到仓库内的 `stop.request` 或监管进程信号。限时验收仍使用 `--deadline-epoch`，两种模式互斥。

当 React 通过本机 `127.0.0.1:18360` 的 HTTP 隧道访问时，浏览器自己的主机名不是 Kit 主机。生产 React 后端应由同一仓库的启动器携带 `DCDTUNI_OV_SIGNALING_HOST=40901.dynip.ntu.edu.sg` 和 `DCDTUNI_OV_SIGNALING_PORT=8301` 启动；`/api/server-info` 将此端点交给 React。用户仍可在页面的“OV 连接”中手动覆盖。若直接用 40901 主机名访问 React，未配置时仍回退到页面主机名。

```bash
DCDTUNI_OV_SIGNALING_HOST=40901.dynip.ntu.edu.sg \
DCDTUNI_OV_SIGNALING_PORT=8301 \
TMPDIR="$PWD/.codex-tmp" \
./src/start_server.sh --app react --port 8360 --ws-port 8361 \
  --case-dir "$PWD/case/Shinaide_Liquid"
```

在 `/mnt/lv/home/litianyu/mycode/dcdtuni` 运行。**每次启动都先重新确认所选 GPU 空闲**，再将下列 UUID、物理编号和 PCI 地址一起改为该卡的实测值；以下是 2026-09-27 GPU 7 的已验收参数。`RUN` 必须是仓库内尚不存在的新目录。当前 React 生产服务使用 8360/8361，启动 Kit 不重启它。

```bash
RUN="$PWD/.codex-tmp/ov-service-gpu7-20260927-02"
nohup python3 tools/kit_acceptance_harness.py \
  --kit-launcher "$PWD/src/backend/kit-cae/_build/linux-x86_64/release/omni.cae_streaming.kit.sh" \
  --extension-dir "$PWD/src/frontend/omniverse/cfd_demo/kit/extensions" \
  --run-dir "$RUN" \
  --gpu-uuid GPU-aba0999f-5d2d-81c1-c6fe-3d74d52671c2 --gpu-index 7 \
  --gpu-pci 00000000:D1:00.0 --cpus 4,5,6,7 --numeric-threads 2 \
  --signal-port 8301 --media-port 47998 --http-port 8378 \
  --reuse-runtime-cache "$PWD/.codex-tmp/ov-live-gpu7-20260927-01" \
  --service --execute > "$PWD/.codex-tmp/ov-service.supervisor.log" 2>&1 < /dev/null &
```

查看 `$RUN/state.json` 中的 `status`、Kit PID 与被选中的 GPU，确认 8301 监听后，用 React 页面实测视频和场景最终 ACK。停机仅在本服务的目录中创建 `$RUN/stop.request`；监管器只回收自己启动的 Kit/Xvfb 进程组，并写出清理回执。若端口处于 TCP `TIME-WAIT`，等待释放后用新的 `RUN` 重启，不清理其他用户的端口。`nohup` 不提供主机重启后的自动恢复。

不要使用旧 `start_demo.sh` 启动此 React 后端：它固定写 `/tmp`、使用共享 Xvfb 显示号，且默认可能另起 AI worker。常驻模式保留 Kit 正常的 info 输出级别，只降低传输场景正文的 WebRTC 消息通道日志级别；全局 warning 曾让信令端口监听但浏览器连接超时。
