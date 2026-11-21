# OV 渲染切换修复回执（2026-10-01）——带传感器/壁挂空调的工程能进 Omniverse，场景同步更小、有进度

**结论：过门。** 用户报告「OV 的切换始终不成功」。根因有两个，都会在 Kit 接受视频流几秒后把视口打回 Three.js
（Kit 日志表现为连上约 7 s 就 `NVST_R_BUSY` + `Client disconnected`，BUSY 是断开时的副作用，不是原因）：

1. **场景快照里有无工程身份的显示辅助物。** `run/sensorSpheres` 的传感器小球（`isSensorSphere`）和
   `shared/js/supply_flow_anim` 的壁挂空调送风锥（之前无标签）都挂在 `globalGroup` 下，`buildSceneSnapshot`
   抛 `Renderable has no project identity`，OV 初始化失败。凡有传感器（Base604、DAML-Testbed）或壁挂空调
   （DAML-Testbed）的工程都切不过去；Shinaide_Liquid 两者都没有，所以此前的 OV 回执没发现。
2. **`--ov-port` 从未到达浏览器。** `start_server.sh --ov` 只导出信令端口，前端只在同时配置了信令主机时才采用服务器
   给的端口，于是总是拨 8301；任何非默认端口都在 sign-in 失败。

同批改进（不改协议语义）：
- 选中物体是视图状态：走 `updateViewState`，不再整场景重发（Shinaide 约 40 MB、一分多钟，期间其他命令被饿死）。
- 照片类贴图以 JPEG 传（原来在画布上重编码为 PNG，体积约 4 倍）；schema 与 Kit 校验接受 `image/jpeg`，
  字节必须与声明类型一致；同一图片重发时复用编码。
- Kit 对传输来的场景只校验一次（原来 `finish()` 和 `author_scene()` 各一次）；纯数值数组走 C 速度的快路径
  （离线 Shinaide 场景：schema 遍历 2.31 s → 0.17 s）。
- 场景确认时限按图元数放宽：max(240 s, 60 s + 30 ms × 图元数)。
- 视口遮罩显示传输百分比和「场景已送达，Omniverse 正在构建…」。
- server-info 增加 `ovBridge`；未带 `--ov` 启动时，失败提示直接给出重启命令。

环境：40901，worktree `~/scratch/wt-e8` @ `fix/ov-switch`，临时 8390（Kit 信令 8303，避开用户默认 8301），用完关闭。

## 验证

| 项 | 结果 |
|---|---|
| vitest（`fbbfe158`） | 321 文件 / 3184 通过，1 跳过；tsc 无错 |
| 新增前端测试 | 传感器球、送风锥（均用真实模块构建）不进快照；JPEG/PNG 按来源编码且重发复用；选中不改场景键；端口无主机也生效；确认时限随图元数增长 |
| Kit 桥 + server 测试（40901，`dcdtuni` env） | 223 通过 / 1 跳过（含新增 JPEG 缓存/误标拒绝、数值快路径与逐元素路径同拒） |
| 全部 25 个 `case/` 工程的 OV 快照（浏览器内构建，不连 Kit） | 修复前 Base604、DAML-Testbed 失败；修复后 25/25 生成 |

## 浏览器门（真实 Kit，界面切换）

| 工程 | 图元 | 结果 | 用时 |
|---|---|---|---|
| Base604（7 个传感器） | 10 276 | 修复前 4 s 内掉回；修复后进入 OV | 189 s（传输 ~6 s，Kit 解包+校验 9 s，建 USD ~117 s，首帧编译余下） |
| DAML-Testbed（10 传感器 + 3 组送风锥） | — | 进入 OV | 16 s |
| Tri_angle（切回 Three.js 后再次进入） | 154 | 槽位正常释放，再次进入 | 35 s |
| 选中物体（Base604，OV 中） | — | Kit 只收到 2 条 `updateViewState`，无 `scene begin` | 即时 |

## 未做（后续）

- **大场景建 USD 仍慢**：Base604 一万个图元每个一份材质（Kit：`10276 materials -> 0 after de-duplication`），
  建场景约 2 分钟。按设计每个图元独占材质，供液冷结果按图元着色（`override_colours`，"nothing bleeds"）；
  共享材质需要先改着色路径，或用 Sdf 层 API 批量写。另：编辑（`revision` 变化）仍会整场景重发，可改为几何指纹键。
- 纹理按内容哈希在 Kit 侧去重（跨重发不再传已有贴图）。
