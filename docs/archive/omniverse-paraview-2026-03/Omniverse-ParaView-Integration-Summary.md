# Omniverse + ParaView 集成方案总结

## 1. 背景

我们的 CFD 后处理链路目前有三个渲染端：

| 渲染端 | 技术栈 | 优势 | 局限 |
|--------|--------|------|------|
| 浏览器 (STL_generate.html) | Three.js WebGL | 轻量、即开即用 | 渲染质量有限，无体渲染 |
| ParaView | VTK OpenGL/OSPRay/OptiX | 专业 CFD 可视化标杆 | 独立桌面程序，无法嵌入 |
| Omniverse (Kit App) | RTX 光追 + USD | 影视级渲染、多人协作 | 需要 GPU 服务器 |

NVIDIA 官方提供了 **ParaView Omniverse Connector** 插件，使 ParaView 可以将渲染数据实时同步到 Omniverse。这为我们提供了一条将 ParaView 专业可视化能力与 Omniverse RTX 渲染结合的路径。

---

## 2. ParaView Connector 工作原理

### 2.1 数据流（单向）

```
ParaView ──(USD)──> Omniverse Nucleus / 本地 USD 文件
                         │
                         ▼
                    Omniverse Kit App (RTX 渲染)
```

- **单向同步**：ParaView → Omniverse（不可反向）
- **数据格式**：渲染数据（points, normals, texcoords, vertex colors）→ USD prim
- **体数据**：Structured volumes → OpenVDB（`.vdb`）
- **Live Sync**：ParaView 修改 colormap / 参数后，Omniverse 实时更新

### 2.2 核心组件

1. **Omniverse Connector Render View** — ParaView 中的特殊视口，自动将可见 actor 转为 USD
2. **Pass Arrays Filter** — 将自定义场变量（温度、压力等）作为 USD primvar 传递
3. **Temporal Arrays Filter** — 标记时间步相关/无关的数组，避免重复数据
4. **Live Sync Layer** — 在 Nucleus 上创建 `.live` 层，Kit App 可实时读取

### 2.3 支持的可视化类型

- Color-mapped surface meshes（我们的温度场/速度场）✓
- Slice representations（切面）✓
- Streamlines / tubes（流线）✓
- Volume rendering（体渲染，需 structured → OpenVDB）✓
- Point clouds ✓
- Animated timestep sequences ✓
- MPI 分布式环境 ✓
- Headless (EGL) 服务器 ✓

### 2.4 限制

- 导出是 **单向** 的 — ParaView 无法从 Omniverse 导入
- Live Sync 不支持创建/删除 pipeline nodes（只支持修改已有节点的参数）
- 仅支持 ParaView **5.12**（当前最新支持版本）
- Volume 仅支持 structured grids（其他类型需先用 `Resample To Image` 转换）

---

## 3. 我们的 CFD 后处理集成方案

### 方案 A：双 GUI 模式（ParaView + Omniverse 并行）

**架构：**

```
用户操作 ParaView                      Omniverse Kit App
    │                                       │
    ├─ 加载 OpenFOAM case                    │
    ├─ 创建切面/流线/体渲染                    │
    ├─ 开启 Connector Render View             │
    ├─ 点击 Live Sync ─────────────────> 实时同步 USD
    ├─ 调整 colormap/clim ─────────────> RTX 重新渲染
    │                                       │
    │                              ┌────────┤
    │                              │ RTX 光追渲染
    │                              │ PBR 材质
    │                              │ 多人协作浏览
    │                              └────────┘
```

**优势：**
- 零开发成本 — 直接使用 NVIDIA 官方插件
- ParaView 完整功能（专业 CFD 后处理）
- Omniverse RTX 渲染质量
- Live Sync 实时联动

**劣势：**
- 需要同时打开两个桌面应用
- ParaView 在远程服务器上需要 X11 forwarding 或 VNC
- 单向同步 — Omniverse 侧不能反向操作 ParaView

**适用场景：** 高质量渲染展示、多人评审会议、RTX 效果截图/视频

### 方案 B：ParaView Python Headless + Connector 自动化

**架构：**

```
dctwin server.py
    │
    ├─ 求解完成 → 触发 Python 脚本
    │
    ▼
ParaView pvpython (headless, 无 GUI)
    │
    ├─ from paraview.simple import *
    ├─ 加载 OpenFOAM case
    ├─ 创建 OmniConnectRenderView
    ├─ 自动创建切面/流线/体渲染
    ├─ 输出 USD 到本地目录
    │
    ▼
Omniverse Kit App 加载 USD
    ├─ RTX 渲染
    └─ 用户在 OV 中交互浏览
```

**优势：**
- 全自动化 — 求解完成自动触发后处理
- 不需要打开 ParaView GUI
- 可集成到 CI/CD 流水线

**劣势：**
- 需要在服务器安装 ParaView 5.12 + Connector
- 预设的可视化方案（切面位置/colormap 等），灵活性不如交互式
- MPI headless 配置较复杂

**适用场景：** 批量后处理、自动报告生成、CI/CD 集成

### 方案 C：Omniverse 内嵌 ParaView 串流窗口（高级）

**构想：**

```
Omniverse Kit App
  ┌─────────────────────────────────────────┐
  │  主视口 (RTX 渲染 USD 场景)               │
  │                                         │
  │    ┌─────────────────────┐              │
  │    │ ParaView 串流窗口    │              │
  │    │ (WebRTC/VNC)        │              │
  │    │                     │              │
  │    │ ┌─ ParaView GUI ─┐ │              │
  │    │ │ 切面/流线/体渲染  │ │              │
  │    │ └────────────────┘ │              │
  │    └─────────────────────┘              │
  │                                         │
  │  Live Sync ←──── Connector ───→ USD 场景 │
  └─────────────────────────────────────────┘
```

**技术路线：**

1. **ParaView 端**：启用 ParaView Web (`pvw`) 或 VNC，暴露一个 WebSocket/HTTP 串流端口
2. **Kit 扩展端**：开发 `stlgen.paraview` 扩展，使用 `omni.ui.ImageProvider` 或 `omni.ui.WebView`（如果 Kit 支持）将 ParaView 的串流画面嵌入一个浮动面板
3. **数据同步**：ParaView 同时运行 Omniverse Connector，修改操作自动同步到 Kit 主视口的 USD 场景

**技术可行性分析：**

| 组件 | 可行性 | 说明 |
|------|--------|------|
| ParaView Web (pvw) | ✓ 成熟 | ParaView 内置 Web 服务端，支持 WebSocket + 图像串流 |
| VNC 远程桌面 | ✓ 成熟 | 通用方案，延迟较高 |
| Kit `omni.ui` 嵌入 | ? 需验证 | `omni.ui` 是否有 WebView widget 需要实测；替代方案是用 `ImageProvider` + 自定义 WebSocket 客户端 |
| 同时运行 Connector + Web | ✓ 可行 | ParaView 支持同时开多个 Render View |
| 延迟 | 中等 | pvw 图像串流延迟约 100-300ms，足够交互 |

**开发工作量：** 中等（约 2-4 周）
- Kit 扩展：WebSocket 客户端 + ImageProvider 显示 (~500 行)
- ParaView 端：pvw 启动脚本 + 自动加载 Connector (~200 行)
- 整合：dctwin server.py 生命周期管理 (~300 行)

**优势：**
- 单一应用窗口体验
- ParaView 完整操作能力 + Omniverse RTX 渲染
- 用户在 OV 中修改 → ParaView 执行 → Connector 同步回 OV，形成闭环

**劣势：**
- 开发量较大
- 依赖 pvw 或 VNC 的延迟
- Kit WebView 能力需实测确认

**适用场景：** 终极集成方案、产品级演示

---

## 4. 推荐路线

### 短期（立即可用）：方案 A

直接使用 ParaView 5.12 + Omniverse Connector 双窗口模式。操作步骤：

1. 安装 ParaView 5.12 + Connector 插件（[安装指南](../../Ref_code/ParaView_Omniverse_Connector/installation.md)）
2. ParaView 打开 OpenFOAM case（`case.foam`）
3. 创建 Omniverse Connector Render View
4. 设置输出路径（Nucleus 或本地 USD）
5. 开启 Live Sync
6. Omniverse Kit App 打开对应 USD

### 中期（1-2 周）：方案 B

将 pvpython 脚本集成到 dctwin 的求解后流程：

```python
# server.py 求解完成后自动触发
pvpython_cmd = f"""
from paraview.simple import *
reader = OpenFOAMReader(FileName='{case_dir}/case.foam')
reader.UpdatePipeline()
# ... 创建切面/流线 ...
# ... 开启 Connector Render View ...
# ... 输出 USD ...
"""
```

### 长期（探索）：方案 C

评估 `omni.ui` 的 WebView/ImageProvider 能力，如果可行，开发 ParaView 串流嵌入扩展。

---

## 5. 与我们现有系统的关系

```
                    ┌─ 浏览器 (Three.js)      ← 轻量预览，即开即用
                    │
dctwin 求解结果 ────┼─ ParaView (VTK)          ← 专业 CFD 后处理
 (OpenFOAM case)    │      │
                    │      └── Connector ──→ Omniverse (RTX)  ← 影视级渲染
                    │
                    └─ 直接 USD 导出           ← 未来路线（跳过 ParaView）
```

三个渲染端面向不同场景：
- **浏览器**：日常调试、快速查看（已实现）
- **ParaView**：专业分析、论文图表（已可用）
- **Omniverse**：高质量展示、多人协作、RTX 渲染（通过 Connector 桥接）

---

## 6. 参考资源

- [ParaView Connector 官方文档](https://docs.omniverse.nvidia.com/connect/latest/paraview.html)
- [ParaView Connector GitHub](https://github.com/NVIDIA-Omniverse/ParaViewConnector)
- [ParaView Web (pvw) 文档](https://kitware.github.io/paraviewweb/)
- [Omniverse Kit 串流](https://docs.omniverse.nvidia.com/kit/docs/kit-manual/latest/guide/livestream.html)
- [本地参考文档](../../Ref_code/ParaView_Omniverse_Connector/)
- [ParaView 源码参考](../../Ref_code/ParaView/)（LightKit、渲染管线）
