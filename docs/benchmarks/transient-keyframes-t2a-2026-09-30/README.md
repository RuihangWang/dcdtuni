# 瞬态关键帧 T2a 回执(2026-09-30)——冻结正确性、顶栏稳态/瞬态切换、退役旧瞬态页

**结论:过门。** T1 的"t ≠ 0 冻结场景"只拦了 `store.edit`,就地修改(`notifyLiveEdit`)与拖拽(`mutateLive`)能绕过它;
T2a 把冻结落到所有写路径,加上顶栏「稳态 / 瞬态」切换(唯一写入者,同步镜像 `solverType`),并删除已无读者的「⏱ 瞬态」页。
机读回执 `receipt.json`。

环境:40901,`src/start_server.sh --build --app react --port 8390`,React 构建 `ee5f1f42`(分支 `feat/transient-t2a`);
内置浏览器窗格后台标签页经 Mac 隧道(8392 标签页留给 base604 复现登录,未动)。8360/8361 全程未触碰。

## 用户裁决(本阶段)

1. 冻结范围确认:只冻结场景状态(顶层 `geometry` + `constructions`);case 目录、求解参数、探针、模型库随时可改。
2. 纯显示字段(`meta.display`、面 `opacity`/`wallOpacities`、`moveTool`/`rotateTool`)随时可改——登记表 `displayOnly` 白名单。

## 测试

| 套件 | 结果 |
|---|---|
| vitest | 302 文件 / 3082 测试全过(删掉旧瞬态页 3 个测试文件与一组用例;新增冻结 11 项、NumberField 2 项、模式切换 4 项、自动化 1 项) |
| `tsc -b` / `vite build` | 干净 / 通过(40901 `--build`) |
| node `test_cfdproj_transient.mjs` | 23/23(新增纯显示白名单 1 项);其余 node 文件失败集合与 main 相同(7 个既有) |
| Python(40901) | 见 `receipt.json`(transient 孪生 + 奇偶 + 混合换热交接 + headless 加载路由) |

变异检查:去掉 `notifyLiveEdit` 守卫 → 还原用例失败;去掉 undo 后的参照刷新 → undo/redo 用例失败;去掉工具开关的 `{ scene: false }` → 真实写入者用例失败。

## 浏览器冒烟(40901,Shinaide_liquid)

| # | 项 | 结果 |
|---|---|---|
| 1 | 顶栏出现切换 | `#transient-mode-selector` 位于 `#toolbar`,选项「稳态 / 瞬态」;旧 `#tab-btn-transient` 已不存在 |
| 2 | 切到瞬态(真实控件) | `transient.mode=transient`,`simulationConfig.solverType=transient`,`endTimeSeconds=300=durationS` |
| 3 | 播放头 120 s(自动化 `setPlayhead`) | `sceneFrozen()=true` |
| 4 | 属性面板改冷板功耗 100→180 | 120 s 处关键帧 `{t:120, value:180, rampS:0}`,预设仍为 `cpu`,无拒绝 |
| 5 | 属性面板改"距 inlet 面" 0.120→0.2(静态,走 `notifyLiveEdit` 旧路径) | 被拒并还原,输入框回到 0.120,日志 `editRejected rackDetailServerDepth: static at t=120s` |
| 6 | 拖服务器透明度滑条到 37%(纯显示) | 接受,存为 `meta.display.opacity=0.37` |
| 7 | 冻结时场景拖拽(`mutateLive` 改机柜位置) | 不生效 |
| 8 | 120 s 处撤销两步 / 重做 | 关键帧消失、功耗回 100 / 恢复 180 |
| 9 | 保存到 `/tmp/t2agate/` 并重开 | 模式瞬态、选择器与 `solverType` 一致;播放头回 0;t=0 功耗 100、t=120 为 180;预设 `cpu`;透明度 0.37;磁盘上 `Q_W` 已剥除 |

case 目录未被改写(`git status case/` 仅既有未跟踪的渲染场景文件)。

## 与计划不同之处

- **A3 改为一处守卫**:不在 7 个拖拽入口各自拦,而是 `mutateLive` 在冻结时对场景修改直接不执行;22 处非场景调用显式传 `{ scene: false }`。
  覆盖全部拖拽入口(含计划未列出的);把手本身仍会被拖动,隐藏把手留给 T2c。
- **A4 无需改代码**:提交的 blur 同时结束 `editing`,其重同步已把被拒的数值还原;加了回归测试钉住。
- 顶栏选项用「稳态 / 瞬态」(用户原话)的新键;求解页单选仍是「定常 / 非定常」。

## 已知 / 交给 T2b、T2c

- 被拒时目前只写日志,界面上没有提示;只读外观与提示在 T2c。
- t ≠ 0 时顶栏送/回风汇总会显示不平衡(回风在 t ≠ 0 不再镜像送风,编译时由送风轨道推导,T4)。
- 进度条、轨道面板(T2b);求解页与液冷实验面板瘦身(T2c)。
