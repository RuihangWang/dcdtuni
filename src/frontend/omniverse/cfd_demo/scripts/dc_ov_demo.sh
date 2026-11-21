#!/usr/bin/env bash
#
# OV CFD Demo 进程管理脚本 (Omniverse kit-cae streaming backend + web UI)
# =====================================================================
# 用途：一键开关 Omniverse CFD demo 的两个进程：
#   1. Kit 后端 (kit-cae streaming + dc.cfd_bridge，渲染 + WebRTC 推流，信令端口 8301)
#   2. Web 前端 (Vite dev server，端口 8081)
#
# 用法：
#   ./dc_ov_demo.sh start          # 启动 Kit 后端 + Web 前端
#   ./dc_ov_demo.sh stop           # 停止两者
#   ./dc_ov_demo.sh restart        # 重启两者
#   ./dc_ov_demo.sh status         # 查看状态 + 访问地址
#   ./dc_ov_demo.sh kit  {start|stop|restart}   # 只操作 Kit 后端
#   ./dc_ov_demo.sh web  {start|stop|restart}   # 只操作 Web 前端
#   ./dc_ov_demo.sh logs {kit|web}              # tail 日志
#
# 备注：
#   - Kit 后端冷启动约 90s；用 `status` 或 `logs kit` 观察 "native UI hidden"。
#   - 仅用 `fuser -k <port>/tcp` 终止进程（共享主机禁止 kill/pkill）。
#   - GPU 默认 3，可用 CFD_GPU=N 覆盖（透传给 start_demo.sh）。
set -uo pipefail

# Derived from this script's own location, never hardcoded: the previous absolute
# path meant a clone anywhere else (different user, different mount) died at the
# `cd "$BUILD"` below with "No such file or directory" before Kit ever started.
# scripts -> cfd_demo -> omniverse -> frontend -> src -> repo root, i.e. five levels.
# DC_REPO_ROOT overrides it for unusual layouts.
ROOT="${DC_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)}"
DEMO="$ROOT/src/frontend/omniverse/cfd_demo"
KIT_PORT=8301          # WebRTC 信令端口（浏览器据此连接渲染流）
WEB_PORT=8081          # Vite dev server
KIT_LOG=/tmp/dc_cfd_demo.kit.log
WEB_LOG=/tmp/dc_cfd_demo.web.log

_ip()   { hostname -I 2>/dev/null | awk '{print $1}'; }
_up()   { fuser "$1/tcp" >/dev/null 2>&1; }

start_kit() {
  if _up "$KIT_PORT"; then echo "[kit] 已在运行 (:$KIT_PORT)"; return; fi
  echo "[kit] 启动中… (冷启动约 90s)"
  bash "$DEMO/scripts/start_demo.sh"
  echo "[kit] 日志: $KIT_LOG  —  等待 'native UI hidden' 表示就绪"
}

start_web() {
  if _up "$WEB_PORT"; then echo "[web] 已在运行 (:$WEB_PORT)"; return; fi
  echo "[web] 启动 Vite (:$WEB_PORT)…"
  ( cd "$DEMO/web" && nohup npm run dev >"$WEB_LOG" 2>&1 & )
  sleep 2
  echo "[web] 日志: $WEB_LOG"
}

stop_kit() { if _up "$KIT_PORT"; then fuser -k "$KIT_PORT/tcp" 2>/dev/null; echo "[kit] 已停止"; else echo "[kit] 未运行"; fi; }
stop_web() { if _up "$WEB_PORT"; then fuser -k "$WEB_PORT/tcp" 2>/dev/null; echo "[web] 已停止"; else echo "[web] 未运行"; fi; }

status() {
  local ip; ip="$(_ip)"
  _up "$KIT_PORT" && echo "[kit] UP    信令 :$KIT_PORT" || echo "[kit] DOWN"
  _up "$WEB_PORT" && echo "[web] UP    :$WEB_PORT"        || echo "[web] DOWN"
  echo
  echo "从笔记本访问（二选一）:"
  echo "  A) 同网段可直连主机 IP:   http://$ip:$WEB_PORT"
  echo "  B) 仅 SSH 隧道转发:       ssh -L $WEB_PORT:localhost:$WEB_PORT -L $KIT_PORT:localhost:$KIT_PORT litianyu@$ip"
  echo "                            然后本地浏览器打开 http://localhost:$WEB_PORT"
  echo "  注：网页 + WebRTC 渲染流需同时可达 $WEB_PORT 与 $KIT_PORT。"
}

case "${1:-}" in
  start)   start_kit; start_web; echo; status ;;
  stop)    stop_web; stop_kit ;;
  restart) stop_web; stop_kit; sleep 3; start_kit; start_web; echo; status ;;
  status)  status ;;
  kit) case "${2:-}" in
         start) start_kit ;; stop) stop_kit ;; restart) stop_kit; sleep 3; start_kit ;;
         *) echo "用法: $0 kit {start|stop|restart}"; exit 1 ;; esac ;;
  web) case "${2:-}" in
         start) start_web ;; stop) stop_web ;; restart) stop_web; sleep 1; start_web ;;
         *) echo "用法: $0 web {start|stop|restart}"; exit 1 ;; esac ;;
  logs) case "${2:-}" in
          kit) tail -f "$KIT_LOG" ;; web) tail -f "$WEB_LOG" ;;
          *) echo "用法: $0 logs {kit|web}"; exit 1 ;; esac ;;
  *) echo "用法: $0 {start|stop|restart|status|kit ...|web ...|logs ...}"; exit 1 ;;
esac
