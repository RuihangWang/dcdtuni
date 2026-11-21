# 旧液冷工作树清理归档

用户于 2026-09-28 授权移除 `/mnt/lv/home/litianyu/mycode/dcdtuni-liquid-platform`。该目录是主仓库的 Git linked worktree，旧分支 `codex/liquid-control-platform@8923e002` 已全部包含在主仓库 `main`，不是独立且尚未合并的源码库。此归档仅保存清理证据，不恢复开发或实验。

清理前停止旧服务 8352/8353；112 个修改或未跟踪的非缓存文件与主仓库副本 SHA-256 相同。补迁两次 LiquidSingle_Control 运行的 6 个输入、结果和检查点 JSON 至主仓库同名路径，另将 40 份历史调试/审查记录逐字节保存于此处 `sdd/`。3 个旧登录状态或服务日志文件在主仓库内 `.codex-tmp/liquid-platform-cleanup-2026-09-28/private/` 私下备份，权限限制为仅当前用户读取，不纳入 Git。

[迁移清单](manifest.json)记录原路径、目标路径、SHA-256 和字节数。node_modules、Python bytecode、pytest cache、React dist 和 TypeScript 构建缓存共 7907 个文件可由源码重建；不重复归档。旧工作树 236 个 Git LFS 对象均存在于主仓库共享对象库，旧分支保留以供历史查阅。原有不同版本的已跟踪文件仍可从共享 Git 历史恢复。

旧目录用 `git worktree remove --force` 移除，不手工删除主仓库或其它工作树。只关闭引用旧目录的项目服务，不终止 VS Code 的闲置终端进程；该终端之后应切换到主仓库目录。
