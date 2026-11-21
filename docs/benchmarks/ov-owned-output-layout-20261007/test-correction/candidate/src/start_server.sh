#!/usr/bin/env bash
#
# dcdtuni 启动器
# ==============
# 一个入口启动三个前端之一，端口只需指定一次：--port 就是你在浏览器里打开的那个端口。
#
#   ./start_server.sh                       # 默认：React 生产版，全部在 8000
#   ./start_server.sh --app react           # React 生产版，全部在 8000
#   ./start_server.sh --dev                 # React 开发版（Vite + HMR，两个端口；--dev 隐含 --app react）
#   ./start_server.sh --build               # 强制重新构建 React 再启动（隐含 --app react）
#   ./start_server.sh --app omniverse       # Omniverse CFD demo（Kit 后端 + React UI）
#   ./start_server.sh --app react --ov      # React + 并启 OV 显示桥（Kit 渲染,WebRTC 信令 :8301,GPU 默认 #0）
#   ./start_server.sh --ov --gpu 3 --ov-port 8302 --ov-stream-port 47999  # OV 桥独立信令/UDP 视频端口
#   ./start_server.sh --port 9000           # 换端口；WS 端口默认为 端口+1
#   ./start_server.sh --dev --api-port 8005 # 开发版复用一个已在跑的后端
#   ./start_server.sh --case-dir /abs/path/to/case  # 其余参数透传给 server.py
#
# 端口分工：
#
#   html（显式选用）     Python 后端同时提供 STL_generate.html 和 /api —— 只占一个端口
#                        → 后端 = --port / --ws-port，浏览器打开 --port/STL_generate.html
#
#   react（默认生产）    Python 后端同时提供界面和 /api —— 只占一个端口
#                        → 后端 = --port / --ws-port，浏览器打开 --port
#                        界面来自 src/frontend/react/dist（缺失时自动构建）
#
#   react --dev          Vite 提供界面并热更新，Python 提供 /api
#                        → 界面 = --port，后端 = --api-port（默认 --port + 2，WS 再 +1）
#                        Vite 把 /api 反代到后端
#
#   omniverse            Kit 信令默认 8301、UDP 视频默认 47998（每个 Kit 须使用独立端口），
#                        React UI = --port
#
# 为什么生产版只占一个端口：自动化通道（/api/browser/*）要用一个头浏览器打开这个
# 页面，而那个浏览器和 /api 必须同源，否则 cookie 与 SSE 都要额外配置。开发版的
# 两端口分工是 Vite 的反代在替它做这件事。
#
# 依赖：含 dcdtuni 包的 conda 环境（setup_env.sh / pip install -e .）
# 停止：Ctrl+C —— 本脚本自己拉起的子进程会一并停掉
#
set -euo pipefail

# ── 计时工具 ──────────────────────────────────────────────
_T0=$(date +%s%N)
_LAST=$(date +%s%N)
step_time() {
    local now label ms_total ms_step
    now=$(date +%s%N)
    ms_total=$(( (now - _T0) / 1000000 ))
    ms_step=$(( (now - _LAST) / 1000000 ))
    label="$1"
    printf '⏱  %-45s  %6d ms  (cumulative %6d ms)\n' "$label" "$ms_step" "$ms_total"
    _LAST=$now
}
# ──────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
step_time "cd to repo root"

# ── 参数解析 ──────────────────────────────────────────────
APP=react         # one default product UI; --app html explicitly opens the archived editor
APP_EXPLICIT=0
PORT=8000
WS_PORT=""
API_PORT=""
NO_BACKEND=0
DEV=0
FORCE_BUILD=0
OV=0            # --ov: react 模式下并启 Omniverse Kit 显示桥(仅显示,不做建模)
OV_GPU=0        # --ov-gpu: Kit 独占的物理显卡号,用户裁决默认 0(覆盖 cfd_demo 的"最后一张卡"默认)
OV_PORT=8301    # --ov-port: Kit WebRTC 信令端口
OV_STREAM_PORT=47998  # --ov-stream-port: Kit UDP 视频端口；并行实例必须各自独立
PASSTHROUGH_ARGS=()

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \?//'
    exit "${1:-0}"
}

while (($# > 0)); do
    case "$1" in
        --app=*)        APP="${1#*=}"; APP_EXPLICIT=1; shift ;;
        --app)          APP="${2:?--app requires react|html|omniverse}"; APP_EXPLICIT=1; shift 2 ;;
        --port=*)       PORT="${1#*=}"; shift ;;
        --port)         PORT="${2:?--port requires a value}"; shift 2 ;;
        --ws-port=*)    WS_PORT="${1#*=}"; shift ;;
        --ws-port)      WS_PORT="${2:?--ws-port requires a value}"; shift 2 ;;
        --api-port=*)   API_PORT="${1#*=}"; shift ;;
        --api-port)     API_PORT="${2:?--api-port requires a value}"; shift 2 ;;
        --no-backend)   NO_BACKEND=1; shift ;;
        --dev)          DEV=1; shift ;;
        --build)        FORCE_BUILD=1; shift ;;
        --ov)           OV=1; shift ;;
        --ov-gpu=*|--gpu=*) OV_GPU="${1#*=}"; shift ;;
        --ov-gpu|--gpu)  OV_GPU="${2:?--gpu/--ov-gpu 需要显卡号}"; shift 2 ;;
        --ov-port=*)    OV_PORT="${1#*=}"; shift ;;
        --ov-port)      OV_PORT="${2:?--ov-port 需要端口号}"; shift 2 ;;
        --ov-stream-port=*) OV_STREAM_PORT="${1#*=}"; shift ;;
        --ov-stream-port) OV_STREAM_PORT="${2:?--ov-stream-port 需要端口号}"; shift 2 ;;
        -h|--help)      usage 0 ;;
        *)              PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

# --dev, --build and --ov without an explicit --app select the React editor.
if (( ! APP_EXPLICIT )) && (( DEV || FORCE_BUILD || OV )); then
    APP=react
fi

case "$APP" in
    react|html|omniverse) ;;
    *) echo "错误：--app 只能是 react / html / omniverse，收到 '$APP'" >&2; exit 2 ;;
esac

[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "错误：--port 必须是整数" >&2; exit 2; }
# Bound the significant digits before arithmetic so huge/zero-padded inputs
# cannot wrap a shell integer or be interpreted as octal.
[[ "$OV_STREAM_PORT" =~ ^0*([1-9][0-9]{0,4})$ ]] &&
    (( 10#${BASH_REMATCH[1]} <= 65535 )) ||
    { echo "错误：--ov-stream-port 必须是 1..65535 的整数" >&2; exit 2; }
OV_STREAM_PORT="${BASH_REMATCH[1]}"
if (( OV )); then
    [[ "$APP" == "react" ]] \
        || { echo "错误：--ov 只支持 --app react（omniverse 模式有自己的 Kit 启动路径）" >&2; exit 2; }
    [[ "$OV_GPU" =~ ^[0-9]+$ && "$OV_PORT" =~ ^[0-9]+$ ]] \
        || { echo "错误：--gpu/--ov-gpu/--ov-port 必须是整数" >&2; exit 2; }
    (( 10#$OV_PORT >= 1 && 10#$OV_PORT <= 65535 )) \
        || { echo "错误：--ov-port 必须在 1..65535" >&2; exit 2; }
    (( ! NO_BACKEND )) && [[ -z "$API_PORT" ]] || {
        echo "错误：--ov 必须由同一启动器启动后端，不能与 --no-backend / --api-port 复用后端（显示授权密钥无法共享）" >&2
        exit 2
    }
    # 显示授权票据:每次启动生成一把 HMAC 密钥,只经环境变量交给本启动器拉起的
    # 后端与 Kit——后端给结果数据集签票,Kit 只按验过签的票据读盘,浏览器无法自造路径。
    DCDTUNI_OV_GRANT_KEY="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    [[ ${#DCDTUNI_OV_GRANT_KEY} -eq 64 ]] || { echo "错误：无法生成 OV 显示授权密钥" >&2; exit 1; }
    export DCDTUNI_OV_GRANT_KEY
    # Kit 可读的数据集根目录(纵深防御,票据之外的第二道闸):仓库 case/ 与 --case-dir 的上级。
    OV_DATASET_ROOTS="$REPO_ROOT/case"
    for ((i = 0; i < ${#PASSTHROUGH_ARGS[@]}; i++)); do
        case "${PASSTHROUGH_ARGS[$i]}" in
            --case-dir) [[ -n "${PASSTHROUGH_ARGS[$((i + 1))]:-}" ]] \
                && OV_DATASET_ROOTS+=":$(dirname "${PASSTHROUGH_ARGS[$((i + 1))]}")" ;;
            --case-dir=*) OV_DATASET_ROOTS+=":$(dirname "${PASSTHROUGH_ARGS[$i]#*=}")" ;;
        esac
    done
    export DC_CFD_DATASET_ROOTS="$OV_DATASET_ROOTS"
fi

# 后端端口。html 版和 react 生产版都是「后端即 Web 服务器」，所以后端就是 UI 端口；
# 只有 react --dev 需要让开两位，因为那时 Vite 占着 UI 端口。
if [[ "$APP" == "html" ]] || [[ "$APP" == "react" && $DEV -eq 0 ]]; then
    BACKEND_PORT="$PORT"
else
    BACKEND_PORT="${API_PORT:-$((PORT + 2))}"
fi
BACKEND_WS_PORT="${WS_PORT:-$((BACKEND_PORT + 1))}"
[[ "$BACKEND_PORT" =~ ^[0-9]+$ && "$BACKEND_WS_PORT" =~ ^[0-9]+$ ]] \
    || { echo "错误：端口必须是整数" >&2; exit 2; }
# --api-port 指向一个已在跑的后端时，不要再起一个。
# --api-port names a backend that is already up, so do not start another. Only
# react --dev has a separate backend to point at; in the single-port modes the
# flag is a mistake worth naming rather than silently ignoring.
if [[ -n "$API_PORT" ]]; then
    if [[ "$APP" == "react" && $DEV -eq 1 ]]; then
        NO_BACKEND=1
    else
        echo "错误：--api-port 只在 react --dev 下有意义；生产版和 html 版的后端就是 --port 本身。" >&2
        exit 2
    fi
fi
step_time "parse CLI args"

# ── 端口占用检测 ──────────────────────────────────────────
CURRENT_USER="$(id -un)"

# 匹配任意监听地址形式：0.0.0.0:P / 127.0.0.1:P / [::]:P / [::1]:P。
# 旧版正则写成 ^([0-9.*]|\[::(\]|.*\])):P$ —— 字符类只匹配一个字符，
# 于是 127.0.0.1:8002 被判成空闲，后端起来后直接 Address already in use。
port_in_use() {
    ss -tln 2>/dev/null | awk 'NR>1 {print $4}' | grep -qE "(^|[]:.0-9])$1\$" \
      && ss -tln 2>/dev/null | awk 'NR>1 {print $4}' | grep -qE ":$1\$"
}

port_own_pids() {
    local raw pid owner
    raw="$(fuser "$1/tcp" 2>/dev/null || true)"
    raw="${raw//$'\n'/ }"; raw="${raw//:/ }"
    for pid in $raw; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        owner="$(ps -o user= -p "$pid" 2>/dev/null | awk '{print $1}')"
        [[ "$owner" == "$CURRENT_USER" ]] && printf '%s\n' "$pid"
    done
}

# 只释放自己的进程。别人的进程一律让开，不越界。
free_port_or_die() {
    local port="$1" label="$2" tries=0
    while port_in_use "$port"; do
        if [[ -z "$(port_own_pids "$port")" ]]; then
            echo "错误：${label} 端口 ${port} 被其他用户占用，请换一个端口（--port / --api-port）" >&2
            exit 1
        fi
        tries=$((tries + 1))
        if (( tries > 3 )); then
            echo "错误：${label} 端口 ${port} 释放失败" >&2
            exit 1
        fi
        echo "释放自己占用的 ${label} 端口 ${port}（第 ${tries}/3 次）..."
        fuser -k "${port}/tcp" 2>/dev/null || true
        sleep 0.4
    done
}
step_time "port helpers"

# ── conda 环境 ────────────────────────────────────────────
resolve_python() {
    local required_env="dcdtuni" env="${CONDA_DEFAULT_ENV:-}" base
    if [[ -z "$env" || "$env" == "base" || "$env" != "$required_env" ]]; then
        [[ -n "$env" && "$env" != "base" ]] && echo "⚠ 当前 conda 环境为 '${env}'，自动切换到 ${required_env}"
        env="$required_env"
    fi
    if [[ -n "${CONDA_EXE:-}" ]]; then
        base="$(dirname "$(dirname "$CONDA_EXE")")"
    elif [[ -n "${CONDA_PREFIX:-}" ]]; then
        if [[ "$env" == "base" || "$CONDA_PREFIX" != */envs/* ]]; then base="$CONDA_PREFIX"
        else base="$(dirname "$(dirname "$CONDA_PREFIX")")"; fi
    else
        base="$(conda info --base 2>/dev/null)"
    fi
    CONDA_ENV="$env"
    CONDA_BASE="$base"
    CONDA_PYTHON="${base}/envs/${env}/bin/python"
    [[ -x "$CONDA_PYTHON" ]] || { echo "错误：找不到 conda 环境 $env 的 Python 解释器" >&2; exit 1; }
}

# OpenFOAM 必须全部来自 conda。用户 shell 里可能 source 过系统 OpenFOAM
# (/usr/lib/openfoam2406/etc/bashrc)，会污染 PATH / LD_LIBRARY_PATH / WM_*。
sanitize_foam_env() {
    local prefix="${CONDA_BASE}/envs/${CONDA_ENV}"
    export PATH="${prefix}/bin:$(echo "$PATH" | tr ':' '\n' | grep -iv 'openfoam' | grep -v '/openmpi/' | tr '\n' ':')"
    export LD_LIBRARY_PATH="$(echo "${LD_LIBRARY_PATH:-}" | tr ':' '\n' | grep -iv 'openfoam' | grep -v '/openmpi/' | tr '\n' ':')"
    export WM_PROJECT_DIR="$prefix"
    # 服务器就是在这个 env 里跑的：CONDA_PREFIX 若沿用启动 shell 的（比如 base），
    # 后端按 $CONDA_PREFIX/etc/bashrc 找 OpenFOAM 会落空，foamToVTK 变成 `source ""`。
    export CONDA_PREFIX="$prefix"
    export CONDA_DEFAULT_ENV="$CONDA_ENV"
    export FOAM_LIBBIN="${prefix}/lib"
    export FOAM_APPBIN="${prefix}/bin"
    export FOAM_ETC="${prefix}/etc"
    export FOAM_MPI=sys-mpich
    export MPI_ARCH_PATH="$prefix"
}

# ── 子进程收尾 ────────────────────────────────────────────
# 只按端口收，绝不用 kill/pkill —— 这是共享机器。
CLEANUP_PORTS=()
OV_RUNTIME_DIRS=()
cleanup_ov_runtime() {
    local runtime="$1" control_port supervisor_pid pids i
    [[ -f "$runtime/supervisor.port" && -f "$runtime/supervisor.pid" ]] || return 0
    read -r control_port < "$runtime/supervisor.port"
    read -r supervisor_pid < "$runtime/supervisor.pid"
    [[ "$control_port" =~ ^[0-9]+$ && "$supervisor_pid" =~ ^[0-9]+$ ]] || return 0
    pids="$(port_own_pids "$control_port")"
    # A stale receipt must never terminate a reused port or another launcher.
    if [[ "$pids" == "$supervisor_pid" ]]; then
        # Normal supervisor termination closes its private Kit/probe sessions.
        fuser -k -TERM "${control_port}/tcp" 2>/dev/null || true
        # A probe cleanup retry plus two owned sessions can require ~15s
        # in the exceptional path. Healthy cleanup returns immediately.
        for i in $(seq 1 200); do
            [[ "$(port_own_pids "$control_port")" == "$supervisor_pid" ]] || return 0
            sleep 0.1
        done
        # Preserve the existing exact-port/UID fallback for a stuck supervisor.
        [[ "$(port_own_pids "$control_port")" == "$supervisor_pid" ]] &&
            fuser -k "${control_port}/tcp" 2>/dev/null || true
    fi
}
cleanup() {
    local p runtime
    for runtime in "${OV_RUNTIME_DIRS[@]:-}"; do
        [[ -n "$runtime" ]] && cleanup_ov_runtime "$runtime"
    done
    for p in "${CLEANUP_PORTS[@]:-}"; do
        [[ -n "$p" && -n "$(port_own_pids "$p")" ]] && fuser -k "${p}/tcp" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

start_ov_bridge() {
    # The supervisor owns a private control socket before Kit starts. Cleanup
    # can reach it before GPU preflight or the slow Kit binds its signaling
    # port; normal cleanup closes its own child sessions and descendants.
    free_port_or_die "$OV_PORT" "Omniverse Kit 信令"
    mkdir -p "$REPO_ROOT/.codex-tmp/ov-runtime"
    local runtime i
    OV_STARTUP_TIMEOUT="${DC_CFD_STARTUP_TIMEOUT:-300}"
    [[ "$OV_STARTUP_TIMEOUT" =~ ^[0-9]+$ ]] &&
        (( OV_STARTUP_TIMEOUT >= 1 && OV_STARTUP_TIMEOUT <= 1800 )) ||
        { echo "错误：DC_CFD_STARTUP_TIMEOUT 必须为 1..1800 秒" >&2; exit 2; }
    runtime="$(mktemp -d "$REPO_ROOT/.codex-tmp/ov-runtime/${OV_PORT}.XXXXXX")"
    OV_RUNTIME_DIRS+=("$runtime")
    OV_CURRENT_RUNTIME="$runtime"
    export DCDTUNI_OV_SIGNALING_PORT="$OV_PORT"
    echo "启动 Omniverse Kit 显示桥（GPU #$OV_GPU · 信令 :$OV_PORT · UDP 视频 :$OV_STREAM_PORT · 日志 $runtime/kit.log）..."
    DC_CFD_GPU="$OV_GPU" DC_CFD_AI=0 DC_CFD_SIGNAL_PORT="$OV_PORT" DC_CFD_STREAM_PORT="$OV_STREAM_PORT" \
        DC_CFD_RUNTIME_DIR="$runtime" DC_CFD_MANAGED=1 DC_CFD_PARENT_PID="$$" \
        bash "$REPO_ROOT/src/frontend/omniverse/cfd_demo/scripts/start_demo.sh" \
        > "$runtime/launcher.log" 2>&1 &
    OV_LAUNCHER_PID=$!
    for i in $(seq 1 100); do
        if [[ -f "$runtime/kit.failure" || -f "$runtime/kit.exit" ]]; then
            [[ -f "$runtime/kit.failure" ]] && cat "$runtime/kit.failure" >&2
            echo "错误：Omniverse Kit 在启动时退出，日志 $runtime/kit.log" >&2
            exit 1
        fi
        if [[ -f "$runtime/supervisor.port" && -f "$runtime/supervisor.pid" ]]; then
            echo "  GPU 预检与 Kit 启动已托管；日志 $runtime/kit.log。"
            return 0
        fi
        if [[ ! -d "/proc/$OV_LAUNCHER_PID" ]]; then
            cat "$runtime/launcher.log" >&2
            echo "错误：Omniverse Kit 启动失败，日志 $runtime/kit.log" >&2
            exit 1
        fi
        sleep 0.1
    done
    echo "错误：Omniverse 启动监督器 10s 内未就绪，日志 $runtime/launcher.log" >&2
    exit 1
}


await_ov_ready() {
    local runtime="$OV_CURRENT_RUNTIME" i
    # Backend starts in parallel. No browser/decoded-frame ACK is needed here:
    # kit.ready certifies that this exact Kit owns its signaling listener.
    for i in $(seq 1 $(( (OV_STARTUP_TIMEOUT + 5) * 10 ))); do
        if [[ -f "$runtime/kit.failure" || -f "$runtime/kit.exit" ]]; then
            [[ -f "$runtime/kit.failure" ]] && cat "$runtime/kit.failure" >&2
            echo "错误：Omniverse Kit 启动未就绪，日志 $runtime/kit.log" >&2
            exit 1
        fi
        if [[ -f "$runtime/kit.ready" ]]; then
            echo "  Kit 信令已就绪：:$OV_PORT（视频与 scene ACK 由浏览器验证）。"
            return 0
        fi
        if [[ ! -d "/proc/$OV_LAUNCHER_PID" ]]; then
            echo "错误：Omniverse 启动监督器已退出，日志 $runtime/launcher.log" >&2
            exit 1
        fi
        sleep 0.1
    done
    echo "错误：Omniverse 启动超过 $OV_STARTUP_TIMEOUT 秒，日志 $runtime/kit.log" >&2
    exit 1
}

start_backend() {
    free_port_or_die "$BACKEND_PORT" "后端 HTTP"
    [[ "$BACKEND_WS_PORT" != "$BACKEND_PORT" ]] && free_port_or_die "$BACKEND_WS_PORT" "后端 WS"
    resolve_python
    sanitize_foam_env
    echo "Python: $CONDA_PYTHON"
    # Editable installs can point to a different checkout. Serve this worktree's
    # backend, matching the frontend and manifests launched from this root.
    PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" "$CONDA_PYTHON" src/server/server.py \
        --port "$BACKEND_PORT" --ws-port "$BACKEND_WS_PORT" "${PASSTHROUGH_ARGS[@]}" &
    BACKEND_PID=$!
    CLEANUP_PORTS+=("$BACKEND_PORT" "$BACKEND_WS_PORT")
    # 等它真的开始监听再继续：Vite 起得比 Python 快，先起 Vite 会让第一批 /api 请求打空。
    local i
    for i in $(seq 1 60); do
        port_in_use "$BACKEND_PORT" && return 0
        if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
            if port_in_use "$BACKEND_PORT"; then
                echo "错误：后端端口 ${BACKEND_PORT} 已被占用（很可能是别的用户，本脚本不会去动它）。" >&2
                echo "      换一个：--api-port <其他端口>，或整体换 --port。" >&2
            else
                echo "错误：后端启动失败，见上方 traceback" >&2
            fi
            exit 1
        fi
        sleep 0.5
    done
    echo "错误：后端 30s 内未开始监听 ${BACKEND_PORT}" >&2
    exit 1
}

banner() {
    echo ""
    echo "========================================="
    # dev 与生产的端口分工完全不同（一个端口还是两个），所以模式行必须说清是哪一种，
    # 否则「后端 = UI 端口」这一行看起来像笔误。
    if [[ "$APP" == "react" ]]; then
        echo "  模式        : react ($( (( DEV )) && echo 'dev / Vite 热更新' || echo '生产 / dist'))"
    else
        echo "  模式        : $APP"
    fi
    printf '  浏览器打开  : http://localhost:%s%s\n' "$PORT" "${1:-}"
    if (( NO_BACKEND )); then
        echo "  后端        : http://localhost:${BACKEND_PORT}  (复用已有进程)"
    elif [[ "$APP" != "omniverse" ]]; then
        echo "  后端        : http://localhost:${BACKEND_PORT}  (WS ${BACKEND_WS_PORT})"
    fi
    if (( OV )); then
        echo "  OV 显示桥   : Kit GPU #$OV_GPU · 信令 :$OV_PORT（首帧约 2 分钟；仅显示,建模仍用 Three.js）"
        echo "                SSH 转发需带 -L ${PORT}:localhost:${PORT} -L ${BACKEND_WS_PORT}:localhost:${BACKEND_WS_PORT} -L ${OV_PORT}:localhost:${OV_PORT}"
    fi
    echo "  启动耗时    : $(( ($(date +%s%N) - _T0) / 1000000 )) ms"
    echo "========================================="
    echo ""
}

case "$APP" in
    react)
        step_time "mode: react$( (( DEV )) && echo ' --dev' )"
        [[ -d src/frontend/react/node_modules ]] \
            || { echo "错误：请先在 src/frontend/react 下执行 npm install" >&2; exit 1; }
        # Kit 启动慢(~2 min),先于后端拉起,让两者并行就绪;失败(kit-cae 未构建/
        # 端口占用)则整个启动器立即退出,不留半套服务。
        (( OV )) && start_ov_bridge

        if (( DEV )); then
            # 开发版：Vite 提供界面并热更新，Python 只提供 /api，两个端口。
            (( NO_BACKEND )) || start_backend
            (( OV )) && await_ov_ready
            free_port_or_die "$PORT" "React UI"
            CLEANUP_PORTS+=("$PORT")
            banner "/"
            # Vite 从环境读取端口与反代目标（见 react/vite.config.ts）
            export DCDTUNI_WEB_PORT="$PORT"
            export DCDTUNI_API_TARGET="http://localhost:${BACKEND_PORT}"
            cd src/frontend/react
            npm run dev -- --host
        else
            # 生产版：先保证 dist 存在，再让 Python 后端把它和 /api 一起端出来。
            #
            # 缺 dist 就自动构建，而不是报错让人自己去跑 npm —— 第一次 clone 之后
            # 启动器就该能直接跑起来。已有 dist 时不自动重建：重建要 ~10s，而每次
            # 起服务都付这个代价，只为了覆盖「上次构建后改过源码」这一种情况，不划算；
            # 那种情况用 --build 明说。
            if (( FORCE_BUILD )) || [[ ! -f src/frontend/react/dist/index.html ]]; then
                if (( FORCE_BUILD )); then
                    echo "构建 React（--build）..."
                else
                    echo "未找到 src/frontend/react/dist，先构建 React..."
                fi
                ( cd src/frontend/react && npm run build ) \
                    || { echo "错误：React 构建失败，见上方输出" >&2; exit 1; }
                step_time "npm run build"
            fi
            start_backend
            (( OV )) && await_ov_ready
            banner "/"
            wait "$BACKEND_PID"
        fi
        ;;

    html)
        # 归档的单文件编辑器。Python 后端自己就是 Web 服务器：没有构建产物、
        # 没有 Vite，页面与 /api 同一个端口。留着它是因为特性对齐审计还没做完，
        # 它是唯一一份还跑得起来的行为参照 —— 读它、比对它，不要在它上面开发。
        step_time "mode: html"
        [[ -f src/frontend/web/STL_generate.html ]] || {
            echo "错误：找不到 src/frontend/web/STL_generate.html" >&2
            echo "      恢复：git checkout legacy-html-editor-last-working -- src/frontend/web" >&2
            exit 1
        }
        PASSTHROUGH_ARGS+=(--app html)
        start_backend
        banner "/STL_generate.html"
        wait "$BACKEND_PID"
        ;;

    omniverse)
        step_time "mode: omniverse"
        DEMO_DIR="$REPO_ROOT/src/frontend/omniverse/cfd_demo"
        [[ -d "$DEMO_DIR/web/node_modules" ]] \
            || { echo "错误：请先在 ${DEMO_DIR}/web 下执行 npm install" >&2; exit 1; }
        start_ov_bridge
        await_ov_ready
        free_port_or_die "$PORT" "Omniverse Web UI"
        CLEANUP_PORTS+=("$PORT")
        banner "/"
        echo "提示：SSH 转发需要同时带上 -L ${PORT}:localhost:${PORT} -L ${OV_PORT}:localhost:${OV_PORT}"
        cd "$DEMO_DIR/web"
        npm run dev -- --port "$PORT" --strictPort --host
        ;;
esac
