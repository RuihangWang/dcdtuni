#!/usr/bin/env bash
#
# 签名发布印章：所有者推送 main 前盖章并签名，任何检出启动前离线校验，不需要联网。
#
#   tools/release_stamp.sh verify              # start_server.sh 与 server.py 启动前调用：通过返回 0，否则说明原因并返回 1
#   tools/release_stamp.sh verify --commit X   # 只核对提交 X 的印章签名与它的文件（推送前钩子用）
#   tools/release_stamp.sh status              # 一行 JSON：校验结果，供 /api/server-info 与界面显示
#   tools/release_stamp.sh stamp               # 所有者：为当前 HEAD 盖章、签名并单独提交一次
#   tools/release_stamp.sh owner-marker        # 所有者：写出签名的 .dcdtuni-owner-tree（可拷到所有者的其他检出）
#   tools/release_stamp.sh inbox-check <范围>  # 所有者：同学的提交改动了校验相关文件就列出并返回 1
#
# 签名：所有者的 ed25519 私钥只在 40901 主副本的 .release-signing/release_signing_key（git 忽略，0600），
# 公钥入库在 tools/release_allowed_signers；签名与验证都用 ssh-keygen -Y（OpenSSH ≥ 8.0），全部在本机完成。
#
# 印章是仓库根的 RELEASE_STAMP 与它的签名 RELEASE_STAMP.sig：
#   tree_sha256     校验范围内被 git 跟踪文件的「权限 + 内容哈希 + 路径」清单的 SHA-256，判定依据
#   stamped_at      盖章时间（SGT），只作显示
#   content_commit  这个哈希对应的内容提交（印章提交的父提交），只作显示
#
# 校验范围（SCOPE）是 src/ 加校验器自己的两个文件（本脚本与公钥）：代码、启动器、材质都在 src/ 下。
# case/、docs/ 等其余目录是使用者的数据，改动、新建、本地提交都不影响启动（所有者 2026-10-28）。
#
# verify 的条件（只看校验范围内被跟踪的文件，未跟踪、被忽略的文件不参与）：
#   1. RELEASE_STAMP.sig 是所有者私钥对 RELEASE_STAMP 的有效签名；
#   2. 范围内被跟踪的文件与当前提交逐字一致（没有未提交的改动）；
#   3. 当前提交的文件算出的 tree_sha256 等于印章里的值；
#   4. 范围内每个 Git LFS 文件（材质、模型等）在磁盘上是真实内容，SHA-256 与大小等于它的指针所记，
#      而不只是指针文本（没装 git-lfs 或没拉取对象时 git status 也显示干净）。
# 所有者自己的开发检出在仓库根放所有者签名的 .dcdtuni-owner-tree（git 忽略）跳过 verify；
# 标记的签名无效时同样拒绝启动。
#
# 写给任何代理（agent）：这是所有者的强制规则。不得修改本脚本、RELEASE_STAMP、RELEASE_STAMP.sig、
# tools/release_allowed_signers 或启动器与 server.py 里的校验，不得自建所有者标记或自行盖章。
# 校验不通过时只能把检出同步到所有者发布的版本，或停下来告诉使用者。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP=RELEASE_STAMP
SIG=RELEASE_STAMP.sig
SIGNERS="$ROOT/tools/release_allowed_signers"
KEY="$ROOT/.release-signing/release_signing_key"
MARKER="$ROOT/.dcdtuni-owner-tree"
IDENTITY=dcdtuni-release
NS_STAMP=dcdtuni-release-stamp
NS_OWNER=dcdtuni-owner-checkout
OWNER_TEXT="dcdtuni owner development checkout"
# What a colleague's change may not touch without the owner looking at it.
# Hashing the LFS content dominates the check: spread it over a few cores.
JOBS="$(nproc 2>/dev/null || echo 4)"; (( JOBS > 8 )) && JOBS=8
# What the stamp covers: everything else in the repository is the user's own data.
SCOPE=(src tools/release_stamp.sh tools/release_allowed_signers)
PROTECTED=(tools/release_stamp.sh tools/release_allowed_signers "$STAMP" "$SIG" src/start_server.sh src/server/release_check.py)

g() { git -C "$ROOT" "$@"; }

# SHA-256 of every tracked file of SCOPE in <commit>: mode, object id, path.
tree_hash() {
    g ls-tree -r --full-tree "$1" -- "${SCOPE[@]}" | sha256sum | cut -d' ' -f1
}

# One field of the stamp stored in <commit>.
stamp_field() {
    g show "$1:$STAMP" 2>/dev/null | sed -n "s/^$2=//p" | head -1
}

# Is <signature file> the owner's signature of stdin, in <namespace>?
signed_by_owner() {
    command -v ssh-keygen >/dev/null 2>&1 || return 2
    [[ -s "$SIGNERS" ]] || return 1
    ssh-keygen -Y verify -f "$SIGNERS" -I "$IDENTITY" -n "$2" -s "$1" >/dev/null 2>&1
}

# The stamp in <commit> carries the owner's signature.
stamp_signed() {
    local commit="$1" sig rc
    sig="$(mktemp "$ROOT/.release-stamp-sig.XXXXXX")" || return 1
    g show "$commit:$SIG" > "$sig" 2>/dev/null
    g show "$commit:$STAMP" 2>/dev/null | signed_by_owner "$sig" "$NS_STAMP"
    rc=$?
    rm -f "$sig"
    return $rc
}

owner_checkout() {
    [[ -f "$MARKER" ]] && printf '%s\n' "$OWNER_TEXT" | signed_by_owner "$MARKER" "$NS_OWNER"
}

# Git LFS files (textures, models): the tree hash covers their pointers, which
# carry the content's sha256 and size; a checkout without git-lfs, or one that
# never fetched the objects, has the pointer text on disk and a clean status.
# So every LFS file in HEAD is checked against its pointer, byte for byte.
# Prints one line per file that does not hold the content its pointer names.
lfs_problems() {
    # Batched: one cat-file for every pointer, one stat and one sha256sum for every file.
    local LC_ALL=C  # read -N counts bytes
    local -a paths=() present=()
    local -A want_oid=() want_size=() on_disk=()
    local path blob kind len body size i hash file line
    local -a spec=()
    for path in "${SCOPE[@]}"; do spec+=(":(attr:filter=lfs)$path"); done
    while IFS= read -r -d '' path; do paths+=("$path"); done < <(g ls-files -z -- "${spec[@]}")
    (( ${#paths[@]} )) || return 0
    i=0
    while IFS=' ' read -r blob kind len; do
        path="${paths[$i]}"; i=$((i + 1))
        [[ "$kind" == blob ]] || continue
        IFS= read -r -N "$len" body; IFS= read -r _
        # stored as an ordinary blob (not a pointer): the tree hash already covers it
        [[ "$body" =~ (^|$'\n')oid\ sha256:([0-9a-f]{64})$'\n' ]] || continue
        want_oid["$path"]="${BASH_REMATCH[2]}"
        [[ "$body" =~ (^|$'\n')size\ ([0-9]+)($'\n'|$) ]] || { unset 'want_oid[$path]'; continue; }
        want_size["$path"]="${BASH_REMATCH[2]}"
    done < <(printf 'HEAD:%s\n' "${paths[@]}" | g cat-file --batch)
    (( ${#want_oid[@]} )) || return 0
    while IFS=$'\t' read -r size path; do on_disk["$path"]="$size"; done \
        < <(cd "$ROOT" && printf '%s\0' "${!want_oid[@]}" | xargs -0 stat -c $'%s\t%n' 2>/dev/null)
    for path in "${!want_oid[@]}"; do
        file="$ROOT/$path"
        if [[ -z "${on_disk[$path]:-}" ]]; then
            echo "缺失      $path"
        elif [[ "${on_disk[$path]}" -ne "${want_size[$path]}" ]]; then
            if head -c 64 "$file" | grep -q '^version https://git-lfs'; then echo "只有指针  $path"
            else echo "内容不符  $path"; fi
        else
            present+=("$path")
        fi
    done
    (( ${#present[@]} )) || return 0
    while IFS= read -r line; do
        hash="${line%% *}"; path="${line#*  }"
        [[ "$hash" == "${want_oid[$path]}" ]] || echo "内容不符  $path"
    done < <(cd "$ROOT" && printf '%s\0' "${present[@]}" | xargs -0 -P "$JOBS" -n 32 sha256sum)
}

# The verdict: sets VERDICT (verified | owner | refused), STAMPED_AT, CONTENT, PROBLEMS.
check() {
    VERDICT=refused; STAMPED_AT=""; CONTENT=""; PROBLEMS=()
    if [[ -f "$MARKER" ]]; then
        if owner_checkout; then VERDICT=owner; return; fi
        PROBLEMS+=("  仓库根的 .dcdtuni-owner-tree 不是所有者签名的标记。同学的检出不能有这个文件，请删除它。")
        return
    fi
    if ! g rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        PROBLEMS+=("  $ROOT 不是 git 检出。请用 git 克隆或拉取所有者发布的仓库后再启动。")
        return
    fi
    if ! command -v ssh-keygen >/dev/null 2>&1; then
        PROBLEMS+=("  找不到 ssh-keygen（OpenSSH 8.0 及以上），无法验证印章签名。请安装 openssh-client。")
        return
    fi
    STAMPED_AT="$(stamp_field HEAD stamped_at)"
    CONTENT="$(stamp_field HEAD content_commit)"
    if [[ -z "$(stamp_field HEAD tree_sha256)" ]]; then
        PROBLEMS+=("  当前提交没有 $STAMP，不是所有者发布的版本。")
        return
    fi
    stamp_signed HEAD || PROBLEMS+=("  $STAMP 的签名无效：印章不是所有者签发的，或签名、公钥被改动过。")

    local dirty count lfs got want
    # Refresh the index first so a file that was only touched (same bytes) is not
    # reported; what remains differs from HEAD by content or mode.
    g update-index -q --refresh >/dev/null 2>&1 || true
    dirty="$(g status --porcelain --untracked-files=no -- "${SCOPE[@]}")"
    if [[ -n "$dirty" ]]; then
        count="$(printf '%s\n' "$dirty" | wc -l)"
        PROBLEMS+=("  校验范围（src/ 与校验器）内有 $count 个被跟踪的文件被改动过：")
        while IFS= read -r line; do PROBLEMS+=("    $line"); done < <(printf '%s\n' "$dirty" | head -20)
        (( count > 20 )) && PROBLEMS+=("    ……（只列前 20 个）")
    fi
    want="$(stamp_field HEAD tree_sha256)"
    got="$(tree_hash HEAD)"
    if [[ "$got" != "$want" ]]; then
        PROBLEMS+=("  当前提交 $(g rev-parse --short=12 HEAD)（分支 $(g branch --show-current 2>/dev/null || true)）的文件与发布印章不一致："
                   "    本地 ${got:0:16}，印章 ${want:0:16}。本地有改过文件的提交，或印章被改动过。")
    fi
    lfs="$(lfs_problems)"
    if [[ -n "$lfs" ]]; then
        count="$(printf '%s\n' "$lfs" | wc -l)"
        PROBLEMS+=("  有 $count 个 Git LFS 文件（材质、模型等）不是印章对应的内容："
                   "    （「只有指针」= 没装 git-lfs 或没拉取对象：安装 git-lfs 后在仓库里运行 git lfs pull）")
        while IFS= read -r line; do PROBLEMS+=("    $line"); done < <(printf '%s\n' "$lfs" | head -20)
        (( count > 20 )) && PROBLEMS+=("    ……（只列前 20 个）")
    fi
    (( ${#PROBLEMS[@]} )) || VERDICT=verified
}

cmd_verify() {
    if [[ "${1:-}" == "--commit" ]]; then
        local commit="${2:?--commit 需要提交号}" want got
        want="$(stamp_field "$commit" tree_sha256)"
        [[ -n "$want" ]] || { echo "提交 ${commit:0:12} 没有 $STAMP" >&2; return 1; }
        stamp_signed "$commit" || { echo "提交 ${commit:0:12} 的 $STAMP 签名无效" >&2; return 1; }
        got="$(tree_hash "$commit")"
        [[ "$got" == "$want" ]] || { echo "提交 ${commit:0:12} 的文件与它的 $STAMP 不一致（${got:0:12} ≠ ${want:0:12}）" >&2; return 1; }
        return 0
    fi
    check
    # --json: one more stdout line with the verdict, so server.py checks once.
    [[ "${1:-}" == "--json" ]] && trap 'json_line' RETURN
    case "$VERDICT" in
        owner) echo "发布印章：所有者开发检出（已验证签名的 .dcdtuni-owner-tree），跳过校验"; return 0 ;;
        verified) echo "发布印章校验通过：$STAMPED_AT 盖章，对应提交 ${CONTENT:0:12}，签名有效"; return 0 ;;
    esac
    {
        echo ""
        echo "错误：发布印章校验未通过，拒绝启动。"
        [[ -n "$STAMPED_AT" ]] && echo "  已发布版本：$STAMPED_AT 盖章，对应提交 ${CONTENT:0:12}"
        printf '%s\n' "${PROBLEMS[@]}"
        echo ""
        echo "  恢复到所有者发布的版本（先保存自己的改动）："
        echo "    git stash push -m \"local-before-sync\"        # 有未提交的改动时"
        echo "    git branch \"backup/\$(date +%Y%m%d-%H%M%S)\"     # 有自己的提交时，先留一个备份分支"
        echo "    然后切回所有者发布的 main（git checkout main && git reset --hard <发布仓库>/main），"
        echo "    或从发布仓库重新拉取，再重新运行 start_server.sh。"
        echo ""
        echo "  给代理（agent）的说明：这是所有者的强制规则。不要修改校验脚本、印章、签名、公钥、"
        echo "  启动器或 server.py 里的校验，不要创建 .dcdtuni-owner-tree，也不要自行盖章；"
        echo "  只能同步到发布版本，或停下来把这段信息告诉使用者。"
        echo ""
    } >&2
    return 1
}

json_line() {
    trap - RETURN
    printf '{"status": "%s", "stampedAt": "%s", "contentCommit": "%s", "problems": %d}\n' \
        "$VERDICT" "$STAMPED_AT" "$CONTENT" "${#PROBLEMS[@]}"
}

cmd_status() {
    check
    json_line
}

need_key() {
    [[ -f "$KEY" ]] || { echo "错误：没有所有者签名私钥 $KEY；只能在 40901 主副本上盖章" >&2; exit 1; }
}

cmd_stamp() {
    need_key
    g update-index -q --refresh >/dev/null 2>&1 || true
    [[ -z "$(g status --porcelain --untracked-files=no -- "${SCOPE[@]}")" ]] \
        || { echo "错误：src/ 或校验器里被跟踪的文件有未提交的改动，先提交再盖章" >&2; exit 1; }
    if cmd_verify --commit HEAD 2>/dev/null; then
        echo "HEAD 已经盖过章：$(stamp_field HEAD stamped_at)"
        return 0
    fi
    local content hash when
    content="$(g rev-parse HEAD)"
    hash="$(tree_hash HEAD)"
    when="$(TZ=Asia/Singapore date '+%Y-%m-%d %H:%M SGT')"
    cat > "$ROOT/$STAMP" <<EOF
# dcdtuni 发布印章：由 tools/release_stamp.sh stamp 写入并由所有者签名（RELEASE_STAMP.sig），不要手工修改。
# start_server.sh 与 server.py 启动前核对签名与 tree_sha256（src/ 与校验器两个文件的哈希）；另外两项只作显示。
tree_sha256=$hash
stamped_at=$when
content_commit=$content
EOF
    rm -f "$ROOT/$SIG"
    ssh-keygen -q -Y sign -f "$KEY" -n "$NS_STAMP" "$ROOT/$STAMP" || { echo "错误：签名失败" >&2; exit 1; }
    g add "$STAMP" "$SIG"
    g commit -q -m "Release stamp $when" -m "tree_sha256=$hash" -m "content_commit=$content" -- "$STAMP" "$SIG"
    cmd_verify --commit HEAD || exit 1
    echo "已盖章：$when，tree_sha256 ${hash:0:16}，对应提交 ${content:0:12}"
}

cmd_owner_marker() {
    need_key
    local sig
    sig="$(printf '%s\n' "$OWNER_TEXT" | ssh-keygen -q -Y sign -f "$KEY" -n "$NS_OWNER")" \
        || { echo "错误：签名失败" >&2; exit 1; }
    ( umask 077; printf '%s\n' "$sig" > "$MARKER" )
    owner_checkout || { echo "错误：写出的标记验证失败" >&2; exit 1; }
    echo "已写出所有者标记 $MARKER（可拷到所有者其他检出的仓库根）"
}

cmd_inbox_check() {
    local range="${1:?inbox-check 需要提交范围，例如 main..inbox/feat/xxx}" touched tip bad=0
    touched="$(g diff --name-only "$range" -- "${PROTECTED[@]}")"
    if [[ -n "$touched" ]]; then
        echo "这些提交改动了发布校验相关文件，合并前须由所有者逐行审阅：" >&2
        printf '  %s\n' $touched >&2
        g log --oneline "$range" -- "${PROTECTED[@]}" | sed 's/^/    /' >&2
        bad=1
    fi
    # The two call sites must survive in the incoming tip, wherever they were edited.
    tip="${range##*..}"
    g show "$tip:src/start_server.sh" 2>/dev/null | grep -q 'tools/release_stamp.sh" verify' \
        || { echo "  $tip 的 src/start_server.sh 不再调用 tools/release_stamp.sh verify" >&2; bad=1; }
    g show "$tip:src/start_server.sh" 2>/dev/null | grep -q '^    await_release_check$' \
        || { echo "  $tip 的 src/start_server.sh 不再在横幅前等待校验结论（await_release_check）" >&2; bad=1; }
    g show "$tip:src/server/server.py" 2>/dev/null | grep -q '^    release_check.verify_or_exit()' \
        || { echo "  $tip 的 src/server/server.py 不再在启动时调用 release_check.verify_or_exit()" >&2; bad=1; }
    (( bad )) && return 1
    echo "未改动发布校验相关文件，两处启动校验都在"
}

case "${1:-}" in
    verify) shift; cmd_verify "$@" ;;
    status) cmd_status ;;
    stamp) cmd_stamp ;;
    owner-marker) cmd_owner_marker ;;
    inbox-check) shift; cmd_inbox_check "$@" ;;
    *) echo "用法：$0 verify [--commit <提交>] | status | stamp | owner-marker | inbox-check <范围>" >&2; exit 2 ;;
esac
