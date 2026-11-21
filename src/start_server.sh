#!/usr/bin/env bash
#
# dcdtuni 启动器
# ==============
# 一个入口启动三个前端之一，端口只需指定一次：--port 就是你在浏览器里打开的那个端口。
#
# 完整版本（React 编辑器 + Python 后端 + Omniverse 显示桥 + SCANO AI 推理 + GPU 风冷求解器，局域网可访问）：
#
#   DCDTUNI_ADMIN_USER=admin DCDTUNI_ADMIN_PASSWORD='<口令>' \
#     ./src/start_server.sh --app react --build --port 8064 --host 0.0.0.0 \
#     --gpu 3,2,4 --ov --ov-port 8302 --ov-stream-port 47999 \
#     --scano
#
#   --gpu [列表]   本次启动可用于 GPU 计算的显卡：序号或 GPU-UUID 的逗号列表；不带值 = 本机全部显卡。
#                  给了 --gpu，求解页才能把求解引擎切换到 GPU 风冷求解器（src/backend/aircooling/gpu_solver，
#                  可微的 Newton-Krylov 求解器；默认引擎仍是 OpenFOAM）。Kit 与 AI 推理没有另外指定显卡时也从这张
#                  列表里取：Kit 用第一张（--ov-gpu 可改），AI 推理用 Kit 之外的第一张（只有一张、或没有并启 Kit
#                  时就是第一张；--ai-gpu 可改）。GPU 求解每次只用一张卡：列表里空闲且空闲显存最多的那张。
#                  上例：Kit 在 3 号卡，AI 推理在 2 号卡，GPU 求解在 3/2/4 里空闲的那张。
#   --build        强制重新构建 React 界面。git pull / 改代码后不必带：界面源码比 dist/ 新时启动器会自动重建
#   --host 0.0.0.0 后端监听所有网卡；不带时后端只听 127.0.0.1，别的机器打不开（参数透传给 server.py）
#   --ov           并启 Kit 显示桥；Kit 独占一张物理卡（--gpu 列表的第一张，或 --ov-gpu N；都没给是 0 号卡），
#                  --ov-port / --ov-stream-port 每个 Kit 独立
#   --scano        启动时加载推荐的 AI 模型并预热（AI 推理的开关）；显卡见 --gpu，或用 --ai-gpu 单独指定
#                  （都没给是 0 号卡；需约 6 GiB 空闲显存，不够时拒绝启动并列出各卡空闲显存）
#   浏览器打开 http://<主机>:8064/ ；Kit 约 2 分钟才就绪，渲染器旁的 OV 按钮显示"OV 启动中…"，
#   点它排队，Kit 就绪后自动切到 Omniverse（后处理在 Kit 里用 kit-cae 原生工具完成）。
#   SSH 转发：-L 8064:localhost:8064 -L 8065:localhost:8065 -L 8302:localhost:8302
#   停止：本终端 Ctrl+C，或另开终端 fuser -k 8064/tcp 8065/tcp 8302/tcp（不要用 kill/pkill）
#
# 其他启动方式：
#
#   ./start_server.sh                       # 默认：React 生产版，全部在 8000
#   ./start_server.sh --app react           # React 生产版，全部在 8000
#   ./start_server.sh --dev                 # React 开发版（Vite + HMR，两个端口；--dev 隐含 --app react）
#   ./start_server.sh --build               # 强制重新构建 React 再启动（隐含 --app react）
#   ./start_server.sh --app omniverse       # Omniverse CFD demo（Kit 后端 + React UI）
#   ./start_server.sh --app api --port 9100 --case-dir /abs/case  # 只有 /api、没有界面（python -m agent.scripted_api 的私有服务器）
#   ./start_server.sh --app react --ov      # React + 并启 OV 显示桥（Kit 渲染,WebRTC 信令 :8301,GPU 默认 #0）
#   ./start_server.sh --ov --gpu 3 --ov-port 8302 --ov-stream-port 47999  # OV 桥独立信令/UDP 视频端口；Kit、AI 推理、GPU 求解都在 3 号卡
#   ./start_server.sh --gpu                 # GPU 风冷求解器可选，本机全部显卡可用（每次求解挑一张空闲的）
#   ./start_server.sh --gpu 1,2             # GPU 风冷求解器可选，只用 1、2 号卡；AI 推理默认在 1 号卡
#   ./start_server.sh --ov --ov-gpu 0       # 只给 Kit 指定显卡，不启用 GPU 求解
#   ./start_server.sh --port 9000           # 换端口；WS 端口默认为 端口+1
#   ./start_server.sh --dev --api-port 8005 # 开发版复用一个已在跑的后端
#   ./start_server.sh --dev --ui-host 127.0.0.1 # UI listener defaults to loopback
#   ./start_server.sh --case-dir /abs/path/to/case  # 其余参数透传给 server.py
#   ./start_server.sh --ai-gpu 2            # AI 推理用 2 号卡（序号、GPU-UUID 或 cpu）；没给时取 --gpu 列表，再没有是 0 号卡
#   ./start_server.sh --scano --ai-gpu 2    # 同时在启动时加载推荐模型并预热，首次 AI 推理不再等加载
#   ./start_server.sh --scano scano_c118_s18 --ai-gpu 1  # 指定模型（checkpoint id）；--scano-gpu 与 --ai-gpu 同义
#
# 发布印章：启动前离线核对 RELEASE_STAMP 的所有者签名、src/ 与校验器里被跟踪的文件无改动且与印章记录的哈希一致、
# LFS 文件是真实内容（case/ 等其余目录是使用者的数据，不参与），否则拒绝启动（tools/release_stamp.sh 打印原因与恢复步骤；不需要联网）。
# 这是所有者的强制规则：代理不得删除、修改或绕开这道校验。
#
# 管理员凭据：优先用 DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD 或 --admin-credentials <0600 文件>；
# 都没给时，自动使用（首次生成）src/server/auth-local/admin_credentials，Agent 也从这里读口令登录。
#
# 端口分工：
#
#   html（显式选用）     Python 后端同时提供 STL_generate.html 和 /api —— 只占一个端口
#                        → 后端 = --port / --ws-port，浏览器打开 --port/STL_generate.html
#
#   react（默认生产）    Python 后端同时提供界面和 /api —— 只占一个端口
#                        → 后端 = --port / --ws-port，浏览器打开 --port
#                        界面来自 src/frontend/react/dist（缺失或比源码旧时自动构建）
#
#   react --dev          Vite 提供界面并热更新，Python 提供 /api
#                        → 界面 = --port，后端 = --api-port（默认 --port + 2，WS 再 +1）
#                        Vite 把 /api 反代到后端
#
#   omniverse            Kit 信令默认 8301、UDP 视频默认 47998（每个 Kit 须使用独立端口），
#                        React UI = --port
#
#   api                  只有 Python 后端和 /api，没有任何界面：不读 react/dist，也不要 STL_generate.html
#                        → 后端 = --port / --ws-port；非 /api 路径一律 404，/api/browser/* 返回 409
#                        （自动化通道要在头浏览器里打开编辑器页面，这个模式没有页面）
#                        脚本化运行（python -m agent.scripted_api）为每次运行起一个这样的私有服务器
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
UI_HOST=127.0.0.1
WS_PORT=""
API_PORT=""
NO_BACKEND=0
DEV=0
FORCE_BUILD=0
OV=0            # --ov: react 模式下并启 Omniverse Kit 显示桥(仅显示,不做建模)
OV_GPU=""       # --ov-gpu: Kit 独占的物理显卡号；没给时取 --gpu 列表的第一张，都没给是 0(用户裁决,覆盖 cfd_demo 的"最后一张卡"默认)
GPU_COMPUTE=0   # --gpu [列表]: 给了才启用 GPU 计算（求解页可选 GPU 风冷求解器）
GPU_SPEC=""     # --gpu 的值：all（不带值）或序号 / GPU-UUID 的逗号列表
GPU_SPEC_RE='^(all|([0-9]+|GPU-[0-9A-Fa-f-]+)(,([0-9]+|GPU-[0-9A-Fa-f-]+))*)$'
GPU_INDICES=()  # --gpu 解析出的显卡序号与 UUID（顺序同列表）
GPU_UUIDS=()
GPU_SOLVER_ON=0   # GPU 风冷求解器本次是否可用（--gpu 且依赖齐全），见 enable_gpu_solver
GPU_SOLVER_WARN=""
OV_PORT=8301    # --ov-port: Kit WebRTC 信令端口
OV_STREAM_PORT=47998  # --ov-stream-port: Kit UDP 视频端口；并行实例必须各自独立
PASSTHROUGH_ARGS=()
AI_GPU=""      # --ai-gpu: SCANO AI 推理进程用的显卡（序号、GPU-UUID 或 cpu）；没给时依次取环境变量 DCDTUNI_SCANO_GPU、--gpu 列表、0 号卡（所有者 10-28 / 11-11）
AI_PRELOAD=""  # --scano [模型]：启动时就加载并预热的模型
AI_GPU_WARN="" # AI 推理所选显卡显存不足时的提示（横幅显示）

usage() {
    # The whole header comment, up to the first line of code.
    sed -n '2,/^set -euo pipefail/p' "$0" | sed '$d' | sed 's/^# \?//'
    exit "${1:-0}"
}

while (($# > 0)); do
    case "$1" in
        --app=*)        APP="${1#*=}"; APP_EXPLICIT=1; shift ;;
        --app)          APP="${2:?--app requires react|html|omniverse|api}"; APP_EXPLICIT=1; shift 2 ;;
        --port=*)       PORT="${1#*=}"; shift ;;
        --port)         PORT="${2:?--port requires a value}"; shift 2 ;;
        --ui-host=*)    UI_HOST="${1#*=}"; shift ;;
        --ui-host)      UI_HOST="${2:?--ui-host requires a bind address}"; shift 2 ;;
        --ws-port=*)    WS_PORT="${1#*=}"; shift ;;
        --ws-port)      WS_PORT="${2:?--ws-port requires a value}"; shift 2 ;;
        --api-port=*)   API_PORT="${1#*=}"; shift ;;
        --api-port)     API_PORT="${2:?--api-port requires a value}"; shift 2 ;;
        --no-backend)   NO_BACKEND=1; shift ;;
        --dev)          DEV=1; shift ;;
        --build)        FORCE_BUILD=1; shift ;;
        --ov)           OV=1; shift ;;
        --ov-gpu=*)     OV_GPU="${1#*=}"; shift ;;
        --ov-gpu)       OV_GPU="${2:?--ov-gpu 需要显卡号}"; shift 2 ;;
        --gpu=*)        GPU_COMPUTE=1; GPU_SPEC="${1#*=}"; shift ;;
        --gpu)          GPU_COMPUTE=1
                        if [[ $# -gt 1 && "$2" =~ $GPU_SPEC_RE ]]; then GPU_SPEC="$2"; shift 2; else GPU_SPEC=all; shift; fi ;;
        --ov-port=*)    OV_PORT="${1#*=}"; shift ;;
        --ov-port)      OV_PORT="${2:?--ov-port 需要端口号}"; shift 2 ;;
        --ov-stream-port=*) OV_STREAM_PORT="${1#*=}"; shift ;;
        --ov-stream-port) OV_STREAM_PORT="${2:?--ov-stream-port 需要端口号}"; shift 2 ;;
        --ai-gpu=*|--scano-gpu=*) AI_GPU="${1#*=}"; shift ;;
        --ai-gpu|--scano-gpu) AI_GPU="${2:?--ai-gpu 需要显卡号、GPU UUID 或 cpu}"; shift 2 ;;
        --scano=*)      AI_PRELOAD="${1#*=}"; shift ;;
        --scano)        if [[ $# -gt 1 && "$2" != -* ]]; then AI_PRELOAD="$2"; shift 2; else AI_PRELOAD=recommended; shift; fi ;;
        -h|--help)      usage 0 ;;
        *)              PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

# GPU compute (--gpu): the cards this launch may compute on. Resolved against nvidia-smi here so a typo fails
# before anything starts; Kit and the AI worker take their card from this list unless they were given their own.
if (( GPU_COMPUTE )); then
    command -v nvidia-smi >/dev/null 2>&1 \
        || { echo "错误：--gpu 需要 nvidia-smi（本机没有可用的 NVIDIA 显卡驱动）" >&2; exit 2; }
    GPU_TABLE="$(nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>/dev/null | tr -d ' ')"
    [[ -n "$GPU_TABLE" ]] || { echo "错误：--gpu：nvidia-smi 没有列出任何显卡" >&2; exit 2; }
    [[ -n "$GPU_SPEC" ]] || GPU_SPEC=all
    [[ "$GPU_SPEC" =~ $GPU_SPEC_RE ]] \
        || { echo "错误：--gpu 只能是显卡序号或 GPU-UUID 的逗号列表（不带值 = 全部显卡），收到 '$GPU_SPEC'" >&2; exit 2; }
    if [[ "$GPU_SPEC" == all ]]; then
        mapfile -t GPU_INDICES < <(cut -d, -f1 <<<"$GPU_TABLE")
        mapfile -t GPU_UUIDS < <(cut -d, -f2 <<<"$GPU_TABLE")
    else
        IFS=, read -ra GPU_TOKENS <<<"$GPU_SPEC"
        for GPU_TOKEN in "${GPU_TOKENS[@]}"; do
            GPU_ROW="$(awk -F, -v g="$GPU_TOKEN" '$1==g || $2==g {print; exit}' <<<"$GPU_TABLE")"
            [[ -n "$GPU_ROW" ]] \
                || { echo "错误：--gpu $GPU_TOKEN 不存在；可用显卡：$(cut -d, -f1 <<<"$GPU_TABLE" | paste -sd, -)" >&2; exit 2; }
            [[ " ${GPU_INDICES[*]} " == *" ${GPU_ROW%%,*} "* ]] && continue
            GPU_INDICES+=("${GPU_ROW%%,*}"); GPU_UUIDS+=("${GPU_ROW#*,}")
        done
    fi
fi
# Kit: --ov-gpu, else the first card of the --gpu list, else card 0.
if [[ -z "$OV_GPU" ]]; then
    if (( GPU_COMPUTE )); then OV_GPU="${GPU_INDICES[0]}"; else OV_GPU=0; fi
fi
# AI inference: --ai-gpu, else DCDTUNI_SCANO_GPU, else the first card of the --gpu list that is not Kit's (the
# first one when Kit is not started or the list has one card), else card 0.
if [[ -z "$AI_GPU" ]]; then
    if [[ -n "${DCDTUNI_SCANO_GPU:-}" ]]; then
        AI_GPU="$DCDTUNI_SCANO_GPU"
    elif (( GPU_COMPUTE )); then
        AI_GPU="${GPU_INDICES[0]}"
        if (( OV )); then
            for GPU_TOKEN in "${GPU_INDICES[@]}"; do
                [[ "$GPU_TOKEN" != "$OV_GPU" ]] && { AI_GPU="$GPU_TOKEN"; break; }
            done
        fi
    else
        AI_GPU=0
    fi
fi
# The GPU air solver is offered by this launch only (enable_gpu_solver sets these once the interpreter is known);
# a value inherited from the caller's environment does not enable it.
unset DCDTUNI_GPU_SOLVER
if (( GPU_COMPUTE )); then
    # the solver's cards are those of --gpu: a list by UUID, all cards as "no restriction"
    unset DCDTUNI_GPU_SOLVER_GPU
    if [[ "$GPU_SPEC" != all ]]; then
        DCDTUNI_GPU_SOLVER_GPU="$(IFS=,; echo "${GPU_UUIDS[*]}")"
        export DCDTUNI_GPU_SOLVER_GPU
    fi
fi

# AI inference: one GPU for the SCANO worker, checked here so a typo fails before anything starts.
if [[ -n "$AI_GPU" ]]; then
    if [[ "$AI_GPU" != cpu && "$AI_GPU" != GPU-* ]]; then
        [[ "$AI_GPU" =~ ^[0-9]+$ ]] || { echo "错误：--ai-gpu 只能是显卡序号、GPU-UUID 或 cpu，收到 '$AI_GPU'" >&2; exit 2; }
        if command -v nvidia-smi >/dev/null 2>&1; then
            nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | tr -d ' ' | grep -qx "$AI_GPU" \
                || { echo "错误：--ai-gpu $AI_GPU 不存在；可用显卡：$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | tr -d ' ' | paste -sd, -)" >&2; exit 2; }
        fi
    fi
    # Free memory the AI worker needs to start (a DCTS room under TTA peaks at about 3.8 GiB; each run then picks
    # whole room or TTA by the memory free at that moment). With --scano the model loads at start,
    # so too little is an error; without it a CFD-only session still starts and the banner warns.
    if [[ "$AI_GPU" != cpu ]] && command -v nvidia-smi >/dev/null 2>&1; then
        AI_MIN_FREE_MIB="${DCDTUNI_SCANO_MIN_FREE_MIB:-6000}"
        AI_FREE_TABLE="$(nvidia-smi --query-gpu=index,uuid,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null \
            | awk -F', *' '{printf "%s %s %d\n", $1, $2, $4-$3}')"
        AI_FREE_MIB="$(awk -v g="$AI_GPU" '$1==g || $2==g {print $3}' <<<"$AI_FREE_TABLE")"
        if [[ -n "$AI_FREE_MIB" ]] && (( AI_FREE_MIB < AI_MIN_FREE_MIB )); then
            AI_GPU_WARN="GPU $AI_GPU 只有 $((AI_FREE_MIB / 1024)) GiB 空闲显存，AI 推理需要 $(((AI_MIN_FREE_MIB + 1023) / 1024)) GiB。各卡空闲：$(awk '{printf "%s%s: %d GiB", (NR>1?", ":""), $1, $3/1024}' <<<"$AI_FREE_TABLE")。请用 --ai-gpu N 选择其他显卡。"
            if [[ -n "$AI_PRELOAD" ]]; then
                echo "错误：$AI_GPU_WARN" >&2; exit 2
            fi
        fi
    fi
    PASSTHROUGH_ARGS+=(--scano-gpu "$AI_GPU")
fi
[[ -n "$AI_PRELOAD" ]] && PASSTHROUGH_ARGS+=(--scano "$AI_PRELOAD")

# --dev, --build and --ov without an explicit --app select the React editor.
if (( ! APP_EXPLICIT )) && (( DEV || FORCE_BUILD || OV )); then
    APP=react
fi

case "$APP" in
    react|html|omniverse|api) ;;
    *) echo "错误：--app 只能是 react / html / omniverse / api，收到 '$APP'" >&2; exit 2 ;;
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
        || { echo "错误：--ov-gpu/--ov-port 必须是整数" >&2; exit 2; }
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

# 后端端口。html 版、api 版和 react 生产版都是「后端即 Web 服务器」，所以后端就是 --port；
# 只有 react --dev 需要让开两位，因为那时 Vite 占着 UI 端口。
if [[ "$APP" == "html" || "$APP" == "api" ]] || [[ "$APP" == "react" && $DEV -eq 0 ]]; then
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
        echo "错误：--api-port 只在 react --dev 下有意义；生产版、html 版和 api 版的后端就是 --port 本身。" >&2
        exit 2
    fi
fi
step_time "parse CLI args"

# ── 发布印章校验 ──────────────────────────────────────────
# 离线核对所有者签名、src/ 与校验器里被跟踪的文件与 LFS 内容，见 tools/release_stamp.sh。它在后台与后续启动步骤
# 并行（约 1 秒），打印横幅之前取结论，不通过就清理退出；server.py 启动时还会自己再核对一次。
mkdir -p "$REPO_ROOT/.codex-tmp"
RELEASE_LOG="$(mktemp "$REPO_ROOT/.codex-tmp/release-check.XXXXXX")"
"$REPO_ROOT/tools/release_stamp.sh" verify >"$RELEASE_LOG" 2>&1 &
RELEASE_PID=$!
await_release_check() {
    [[ -n "$RELEASE_PID" ]] || return 0
    if ! wait "$RELEASE_PID"; then
        cat "$RELEASE_LOG" >&2; rm -f "$RELEASE_LOG"
        exit 1
    fi
    cat "$RELEASE_LOG"; rm -f "$RELEASE_LOG"
    RELEASE_PID=""
}

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
    local required_env="dcdtuni" base prefix="${DCDTUNI_ENV_PREFIX:-}" conda_command=""
    if [[ -n "$prefix" ]]; then
        prefix="$(realpath -m "$prefix")"
        [[ "$prefix" == "$REPO_ROOT/"* && "$(basename "$prefix")" == "$required_env" ]] || {
            echo "错误：DCDTUNI_ENV_PREFIX 必须在当前仓库内并以 dcdtuni 结尾" >&2; exit 1;
        }
    elif [[ -x "$REPO_ROOT/.codex-tmp/envs/dcdtuni/bin/python" ]]; then
        prefix="$REPO_ROOT/.codex-tmp/envs/dcdtuni"
    elif [[ "$(basename "${CONDA_PREFIX:-}")" == "$required_env" && -x "${CONDA_PREFIX:-}/bin/python" ]]; then
        prefix="$CONDA_PREFIX"
    else
        # Non-interactive SSH shells may not expose conda on PATH. Use its
        # standard executable variable when available; discovery failures must
        # be handled here because this launcher uses set -e.
        if command -v conda >/dev/null 2>&1; then
            conda_command=conda
        elif [[ -n "${CONDA_EXE:-}" && -x "$CONDA_EXE" ]]; then
            conda_command="$CONDA_EXE"
        fi
        if [[ -z "$conda_command" ]]; then
            echo "错误：未找到 dcdtuni 环境，且当前 shell 找不到 Conda。请先初始化 Conda 并 conda activate dcdtuni，或将 CONDA_EXE 设为已有 Conda 可执行文件；新克隆请先运行 python3 tools/bootstrap_dcdtuni.py --install。" >&2
            exit 1
        fi
        if ! base="$("$conda_command" info --base 2>/dev/null)" ||
            [[ "$base" != /* || "$base" == *$'\n'* || ! -d "$base" ]]; then
            echo "错误：Conda 无法报告有效安装目录。请检查 Conda/CONDA_EXE 并激活 dcdtuni；新克隆请先运行 python3 tools/bootstrap_dcdtuni.py --install。" >&2
            exit 1
        fi
        prefix="${base}/envs/${required_env}"
    fi
    CONDA_ENV="$required_env"
    CONDA_ENV_PREFIX="$prefix"
    CONDA_PYTHON="$prefix/bin/python"
    [[ -x "$CONDA_PYTHON" ]] || {
        echo "错误：找不到 dcdtuni Python；请运行 tools/bootstrap_dcdtuni.py，或配置仓库内的 DCDTUNI_ENV_PREFIX" >&2; exit 1;
    }
}

# OpenFOAM 必须全部来自 conda。用户 shell 里可能 source 过系统 OpenFOAM
# (/usr/lib/openfoam2406/etc/bashrc)，会污染 PATH / LD_LIBRARY_PATH / WM_*。
sanitize_foam_env() {
    local prefix="$CONDA_ENV_PREFIX"
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
    # 每个 mpirun 都只在本机：UCX 只用共享内存。UCX 的 tcp 传输在 40901
    # (2026-11-10，docker 网桥/虚拟网卡) 上让 MPI_Init 永久挂起，网格与求解卡死；
    # self,sm 在单机上等价且不碰网卡。使用者显式设置的 UCX_TLS 优先。
    export UCX_TLS="${UCX_TLS:-self,sm}"
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
# Port reuse is not process ownership: an old launcher's delayed trap must
# never terminate a replacement service that has already reclaimed this port.
CLEANUP_LAUNCHER_PID=$$
cleanup_proc_start() {
    local pid="$1" raw rest
    local -a fields=()
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    [[ "$(stat -c %u "/proc/$pid" 2>/dev/null)" == "$(id -u)" ]] || return 1
    raw="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 1
    rest="${raw##*) }"
    read -r -a fields <<< "$rest"
    [[ "${fields[19]:-}" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "${fields[19]}"
}
cleanup_launcher_owns_pid() {
    local pid="$1" raw rest depth
    local -a fields=()
    for depth in $(seq 1 64); do
        [[ "$pid" == "$CLEANUP_LAUNCHER_PID" ]] && return 0
        [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 ]] || return 1
        cleanup_proc_start "$pid" >/dev/null || return 1
        raw="$(cat "/proc/$pid/stat" 2>/dev/null)" || return 1
        rest="${raw##*) }"
        read -r -a fields <<< "$rest"
        pid="${fields[1]:-}"
    done
    return 1
}
cleanup_owned_port() {
    local port="$1" pid stamp i
    for pid in $(port_own_pids "$port"); do
        stamp="$(cleanup_proc_start "$pid")" || continue
        cleanup_launcher_owns_pid "$pid" || continue
        [[ "$(cleanup_proc_start "$pid" 2>/dev/null)" == "$stamp" ]] || continue
        kill -TERM "$pid" 2>/dev/null || true
        for i in $(seq 1 20); do
            [[ "$(cleanup_proc_start "$pid" 2>/dev/null)" == "$stamp" ]] || break
            sleep 0.1
        done
        if [[ "$(cleanup_proc_start "$pid" 2>/dev/null)" == "$stamp" ]] && cleanup_launcher_owns_pid "$pid"; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
}

cleanup() {
    local p runtime
    for runtime in "${OV_RUNTIME_DIRS[@]:-}"; do
        [[ -n "$runtime" ]] && cleanup_ov_runtime "$runtime"
    done
    for p in "${CLEANUP_PORTS[@]:-}"; do
        [[ -n "$p" ]] && cleanup_owned_port "$p"
    done
    # Empty arrays and an already released port are successful cleanup.
    return 0
}
trap cleanup EXIT INT TERM

start_ov_bridge() {
    # The supervisor owns a private control socket before Kit starts. Cleanup
    # can reach it before GPU preflight or the slow Kit binds its signaling
    # port; normal cleanup closes its own child sessions and descendants.
    resolve_python
    OMNI_CONFIG_PATH="$("$CONDA_PYTHON" "$REPO_ROOT/tools/bootstrap_dcdtuni.py" --prepare-ov-paths)" || exit 1
    export OMNI_CONFIG_PATH
    export XDG_DATA_HOME="$REPO_ROOT/.codex-tmp/bootstrap-cache/xdg-data"
    export XDG_CONFIG_HOME="$REPO_ROOT/.codex-tmp/bootstrap-cache/xdg-config"
    export XDG_CACHE_HOME="$REPO_ROOT/.codex-tmp/bootstrap-cache/xdg-cache"
    export CUDA_CACHE_PATH="$REPO_ROOT/.codex-tmp/bootstrap-cache/cuda"
    export __GL_SHADER_DISK_CACHE_PATH="$REPO_ROOT/.codex-tmp/bootstrap-cache/gl-shaders"
    export TMPDIR="$REPO_ROOT/.codex-tmp/bootstrap-cache/tmp"
    mkdir -p "$TMPDIR"
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
    # The backend writes handed-off editor scenes here for Kit (scene_handoff.py).
    export DCDTUNI_OV_RUNTIME_DIR="$runtime"
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

ensure_admin_credentials() {
    # Credentials precedence: (1) the DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD
    # environment pair, (2) an explicit --admin-credentials file — both passed
    # through untouched. Otherwise (3) local mode: a per-checkout administrator
    # is generated once in src/server/auth-local/ (0700 dir, 0600 two-line file,
    # git-ignored) and used with that directory as --auth-dir, so a plain
    # ./src/start_server.sh always starts and agents can read the same file to
    # log in. No password is ever printed or committed.
    AUTO_CRED_ARGS=()
    if [[ -n "${DCDTUNI_ADMIN_USER+x}" || -n "${DCDTUNI_ADMIN_PASSWORD+x}" ]]; then
        return 0
    fi
    local arg
    for arg in "${PASSTHROUGH_ARGS[@]}"; do
        case "$arg" in
            --admin-credentials|--admin-credentials=*) return 0 ;;
            --auth-dir|--auth-dir=*)
                echo "错误：传了 --auth-dir 却没有给管理员凭据。请同时给 --admin-credentials <0600 文件>，或设置 DCDTUNI_ADMIN_USER / DCDTUNI_ADMIN_PASSWORD。" >&2
                exit 2 ;;
        esac
    done
    local dir="$REPO_ROOT/src/server/auth-local"
    local file="$dir/admin_credentials"
    if [[ ! -f "$file" ]]; then
        ( umask 077
          mkdir -p "$dir"
          printf 'admin\n%s\n' "$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')" > "$file" )
        echo "已生成本机管理员凭据：$file（用户名 admin，口令在文件第二行）"
    fi
    chmod 700 "$dir"
    chmod 600 "$file"
    AUTO_CRED_ARGS=(--admin-credentials "$file" --auth-dir "$dir")
    echo "管理员凭据：$file"
}

# GPU 风冷求解器（--gpu）：代码在 src/backend/aircooling/gpu_solver，与后端同一个 conda 环境。这里只查它要的包在不在
# （不导入，几十毫秒；包名与 runner/gpu_newton.py 的 REQUIRED_MODULES 一致）：缺包时只关掉这一项并在横幅里说明，
# Kit / AI 推理 / OpenFOAM 求解照常启动。后端凭 DCDTUNI_GPU_SOLVER=1 向界面报告可用，并拒绝未启用时的 gpuNewton 请求。
enable_gpu_solver() {
    local py="$CONDA_PYTHON" missing      # 求解器与后端同一个解释器：每台机器只有一个 dcdtuni 环境
    missing="$(PYTHONNOUSERSITE=1 "$py" -c 'import importlib.util as u
print(" ".join(m for m in ("torch", "warp", "pyamg", "torch_geometric", "h5py", "scipy") if u.find_spec(m) is None))' 2>/dev/null)" \
        || missing="（解释器无法运行）"
    if [[ -n "$missing" ]]; then
        GPU_SOLVER_WARN="GPU 风冷求解器未启用：$py 缺少 ${missing}。安装：\"$py\" -m pip install -e '.[gpu-solver]'（见 setup_env.sh）。"
        return 0
    fi
    export DCDTUNI_GPU_SOLVER=1
    GPU_SOLVER_ON=1
}

start_backend() {
    free_port_or_die "$BACKEND_PORT" "后端 HTTP"
    [[ "$BACKEND_WS_PORT" != "$BACKEND_PORT" ]] && free_port_or_die "$BACKEND_WS_PORT" "后端 WS"
    resolve_python
    (( GPU_COMPUTE )) && enable_gpu_solver
    sanitize_foam_env
    ensure_admin_credentials
    echo "Python: $CONDA_PYTHON"
    # Editable installs can point to a different checkout. Serve this worktree's
    # backend, matching the frontend and manifests launched from this root.
    PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" "$CONDA_PYTHON" src/server/server.py \
        --port "$BACKEND_PORT" --ws-port "$BACKEND_WS_PORT" "${AUTO_CRED_ARGS[@]}" "${PASSTHROUGH_ARGS[@]}" &
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
                echo "错误：后端启动失败，原因见上方" >&2
            fi
            exit 1
        fi
        sleep 0.5
    done
    echo "错误：后端 30s 内未开始监听 ${BACKEND_PORT}" >&2
    exit 1
}

banner() {
    await_release_check
    echo ""
    echo "========================================="
    # dev 与生产的端口分工完全不同（一个端口还是两个），所以模式行必须说清是哪一种，
    # 否则「后端 = UI 端口」这一行看起来像笔误。
    if [[ "$APP" == "react" ]]; then
        echo "  模式        : react ($( (( DEV )) && echo 'dev / Vite 热更新' || echo '生产 / dist'))"
    else
        echo "  模式        : $APP"
    fi
    if [[ "$APP" == "api" ]]; then
        printf '  API         : http://localhost:%s/api/  (无界面，只供脚本调用)\n' "$PORT"
    else
        printf '  浏览器打开  : http://localhost:%s%s\n' "$PORT" "${1:-}"
    fi
    if (( NO_BACKEND )); then
        echo "  后端        : http://localhost:${BACKEND_PORT}  (复用已有进程)"
    elif [[ "$APP" != "omniverse" ]]; then
        echo "  后端        : http://localhost:${BACKEND_PORT}  (WS ${BACKEND_WS_PORT})"
    fi
    if (( OV )); then
        echo "  OV 显示桥   : Kit GPU #$OV_GPU · 信令 :$OV_PORT（首帧约 2 分钟；仅显示,建模仍用 Three.js）"
        echo "                SSH 转发需带 -L ${PORT}:localhost:${PORT} -L ${BACKEND_WS_PORT}:localhost:${BACKEND_WS_PORT} -L ${OV_PORT}:localhost:${OV_PORT}"
    fi
    if [[ "$APP" != "omniverse" ]] && (( ! NO_BACKEND )); then
        local ai_where="${AI_GPU:+GPU ${AI_GPU}}"
        [[ "$AI_GPU" == cpu ]] && ai_where="CPU"
        printf '  AI 推理     : %s · %s\n' "$ai_where" \
            "$([[ -n "$AI_PRELOAD" ]] && echo "启动时加载模型（${AI_PRELOAD}）" || echo "首次推理时加载模型")"
        if [[ -n "$AI_GPU_WARN" ]]; then printf '  \033[33m警告        : %s\033[0m\n' "$AI_GPU_WARN"; fi
        if (( GPU_SOLVER_ON )); then
            printf '  GPU 求解    : 显卡 %s · 求解页可选 GPU 风冷求解器（默认引擎仍是 OpenFOAM）\n' \
                "$([[ "$GPU_SPEC" == all ]] && echo "全部（每次求解挑一张空闲的）" || (IFS=,; echo "${GPU_INDICES[*]}"))"
        elif [[ -n "$GPU_SOLVER_WARN" ]]; then
            printf '  \033[33m警告        : %s\033[0m\n' "$GPU_SOLVER_WARN"
        else
            echo "  GPU 求解    : 未启用（加 --gpu 或 --gpu <显卡列表> 后，求解页可选 GPU 风冷求解器）"
        fi
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
            # 每个端口一份 Vite 依赖缓存：同一工作树的两个 --dev 共用 node_modules/.vite
            # 时会互相重新预构建，另一页随即报 "Failed to fetch dynamically imported module"。
            export DCDTUNI_VITE_CACHE_DIR="node_modules/.vite-port-${PORT}"
            cd src/frontend/react
            npm run dev -- --host "$UI_HOST"
        else
            # 生产版：先保证 dist 是当前源码的构建，再让 Python 后端把它和 /api
            # 一起端出来。
            #
            # 缺 dist，或者任何界面源码（React 源码、构建配置、两个编辑器共用的
            # src/frontend/shared）比 dist/index.html 新，就自动重建：git pull 会把
            # 改动的文件写成新的修改时间，所以拉了新版本直接启动就是新界面，不必
            # 记得带 --build。源码没动时不重建（重建要 ~15 s），检查本身只要几毫秒。
            REACT_DIR=src/frontend/react
            STALE_SRC=""
            if [[ -f $REACT_DIR/dist/index.html ]]; then
                STALE_SRC=$(find $REACT_DIR/src $REACT_DIR/index.html $REACT_DIR/package.json \
                    $REACT_DIR/package-lock.json $REACT_DIR/vite.config.ts src/frontend/shared \
                    -newer $REACT_DIR/dist/index.html -type f -print -quit 2>/dev/null)
            fi
            if (( FORCE_BUILD )) || [[ ! -f $REACT_DIR/dist/index.html ]] || [[ -n $STALE_SRC ]]; then
                if (( FORCE_BUILD )); then
                    echo "构建 React（--build）..."
                elif [[ -n $STALE_SRC ]]; then
                    echo "界面源码比 dist 新（例如 ${STALE_SRC}），先重新构建 React..."
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
        # 归档的单文件编辑器，2026-10-23 起停止维护（只维护 React）。Python 后端
        # 自己就是 Web 服务器：没有构建产物、没有 Vite，页面与 /api 同一个端口。
        # 只作历史参照保留，不再同步任何改动。
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

    api)
        # 只有后端：/api 给脚本化运行（python -m agent.scripted_api）用。没有界面，所以既不需要
        # React 构建，也不需要归档的 STL_generate.html；server.py 对非 /api 路径答 404，
        # 对 /api/browser/* 答 409。
        step_time "mode: api"
        PASSTHROUGH_ARGS+=(--app api)
        start_backend
        banner
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
        export DCDTUNI_VITE_CACHE_DIR="node_modules/.vite-port-${PORT}"   # 每个端口一份 Vite 缓存（同上）
        npm run dev -- --port "$PORT" --strictPort --host "$UI_HOST"
        ;;
esac
