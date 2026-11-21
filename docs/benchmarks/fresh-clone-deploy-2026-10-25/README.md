# 干净环境部署回执（2026-10-25，云端容器，无 GPU）

在一台全新的 Linux 容器上，按 [fresh_clone_deploy.md](../../developer/fresh_clone_deploy.md) 从零部署本仓库，并逐道检查其中的四道运行门槛。机器为 4 核 CPU、15 GB 内存、Ubuntu 24.04，没有 NVIDIA GPU，经代理访问外网。可用的部分全部打通；做不到的部分写明原因与适用范围。

## 结论

| 门槛 | 结果 | 证据 |
|---|---|---|
| 1. 启动 React、打开工程、默认贴图载入 | 通过 | 启动器 `start_server.sh` 在 :8000 拉起 React 产品界面，Slab、DAML-Testbed、Shinaide_Liquid 都能载入，贴图正常（无头 Chromium，软件 WebGL） |
| 2. Three.js 与 OV 互切 | 未做（环境限制） | 无 NVIDIA GPU，Kit-CAE 不能构建；NVIDIA 的 npm 源被网络策略拦截，OV 流媒体 SDK 无法安装 |
| 3. 从本仓库源码构建 dcCart，生成目标网格并通过严格 checkMesh | 通过 | Tri_angle：99,958 个单元，4 个 rank 分区，`status = accepted`、`failedChecks = 0`，网格 12 s |
| 4. 风冷稳态与液冷 DAE 流程及数值验收 | 通过 | Tri_angle 稳态按回风判据 `CONVERGED`（1025 步，脚本化 API 全程 230 s）；液冷见 [全环制造解](../liquid-mms-trajectory-gate-2026-10-25/README.md)，以及浏览器中的稳态、瞬态、PI 回路与检查点续跑 |

另外，`dcCartMesh`、两个求解器 `buoyantBoussinesq{Simple,Pimple}Foam_new` 和 `meshgen-cli` 都由各自的 `deploy.sh --clean` 从源码构建成功。

## 部署中发现并修好的问题

- **dcCart 缺 Boost 头文件。** dcCartMesh 的精确几何用到 Boost.Multiprecision，而 `setup_env.sh` 没有安装它。在没有系统 Boost 的机器上，编译会停在 `boost/version.hpp: No such file`。现在 `setup_env.sh` 从 conda-forge 安装 `libboost-headers` 并检查其存在（提交 `68f5a09d1`）。修复后 dcCart 构建通过，上面的严格网格即由这次构建生成。
- **材质金标测试与 Node 版本有关。** `renderMaterialParity` 在未改动的代码上也会失败，原因是 sRGB→线性颜色由 `Math.pow` 计算，其末位在不同 V8 版本之间不同（本机为 Node 24）。现在与 Kit 端测试一样按 1e-12 容差比较（提交 `7b44d82de`）。
- **契约动词表过期。** `.claude/commands/dc-builder.md` 里的动词表与代码不一致，现已重新生成（提交 `3d1a776a4`）。

## 本环境的限制（不是仓库缺陷）

- **PyTorch 的 CUDA 源被拦截。** `setup_env.sh` 按设计给出警告并继续；液冷依赖改用 conda-forge 的 CPU 版 PyTorch 2.6。这一版要求先 `conda activate`（它的 inductor 读取 `CONDA_PREFIX`）。
- **NVIDIA npm 源被拦截。** 除 `@nvidia/omniverse-webrtc-streaming-library` 外，其余依赖都按锁文件安装。这一个包在本地用桩代替，用本地 Vite 配置构建界面；两者都不入库。因此 bootstrap 停在 `npm ci`，本次没有写出原生构建绑定回执；原生树改为直接用各自的 `deploy.sh` 构建。
- **因环境原因无法运行的测试。**
  - React 测试中，3 个 OV 测试文件需要真实的 SDK 字节。
  - 后端测试中，43 项需要 OpenUSD 的 `pxr`（Kit 运行时自带），3 项需要容器内核不提供的 sealed memfd。
  - 上述测试在未改动的基线提交上同样失败。
  - 其余测试全部通过：后端 2852 项、液冷 761 项、React 4764 项、agent 层 439 项。
  - 唯一例外是液冷的 `test_thermal`（Adam ODIL 研究基线），在基线提交上同样失败。原因已查清：这条优化路径对初值极敏感，初始流量差 ±1e-11 kg/s，最终损失就落在 1.1 与 1823 两处；单次运行“损失 < 1”的门槛不是这一基线可复现的性质。改判据还是改测法，留待所有者决定。

## 已查明并修复：Tri_angle 风侧热量账本差 2.3 %

稳态结果中 ACU 移走 6,393 W，多于服务器加热的 6,250 W。原因是服务器风扇入口的温度边界 `inletOutlet` 把无流量（φ = ±0）的面当作回流，钉在初始温度，经切割单元向房间导热约 118 W。改为 `zeroGradient` 后，相对差由 +2.3 % 降到 −0.11 %。详见 [回执](../tri-angle-heat-gap-2026-10-25/README.md)（提交 `a283c0354`）。

## 复现

```bash
python3 tools/bootstrap_dcdtuni.py --dry-run          # 需要 Node 24.12+、iproute2(ss)、conda
python3 tools/bootstrap_dcdtuni.py --install          # 本环境在 npm ci 处因 NVIDIA 源被拦截而停止
conda activate .codex-tmp/envs/dcdtuni
(cd src/backend/aircooling/third_parties/dcCartMesh && ./deploy.sh --clean --no-test)
(cd src/backend/aircooling/third_parties/foam && ./deploy.sh --clean --no-test)
(cd src/backend/aircooling/third_parties/meshgenerator && ./deploy.sh --clean)
cd src && python -m backend.api air ../case/Tri_angle/Tri_angle.cfdproj \
    --mesh-processes 4 --solve-processes 4 --max-processes 4 --require-check-mesh
```
