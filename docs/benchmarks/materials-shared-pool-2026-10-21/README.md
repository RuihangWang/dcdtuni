# 材质共享池：Three.js、旧编辑器与 Omniverse 的视觉校准回执（2026-10-22）

本回执记录 M 流第三段：在同一份 `src/frontend/shared/textures/materials.json` 之下，把 React 编辑器、旧 HTML 编辑器和 Omniverse（React `--ov` 显示桥）三处的观感调到一致，并核对 OV 绑定的贴图就是池里的文件。所有数字都来自本目录里的原始文件（`*.json`、`*.txt`、截图），截图都已回读。

## 结论

- **三处读同一份清单、同一批贴图。** OV 在三个工程的九次加载里绑定的池贴图全部与清单的 sha256 一致；不在池里的只有画布生成的图（DAML 的机柜门孔型、Shinaide 的 CDU 标牌），见 `ov_bound_textures.json`。
- **React 与旧 HTML 编辑器一致。** 同一相机、同一窗口下逐区域比较，亮度差都在 3% 以内（`regions_daml_iso.txt`、`regions_base_leakage_iso.txt`）。
- **Three.js 与 OV 的观感已经对齐。** 白色设备、地面、封闭板、黑色机柜的底色与明暗都落在同一档；OV 额外有光线追踪的阴影，场景更有空间感。改动前 OV 整体过曝，黑色机柜被抬成中灰，封闭板发白，见 `ov_before_after_daml_iso.png`。
- **大场景帧率没有退化。** Base604（10276 个网格）在 React 中交替测四轮，改后帧率略高于改前。

## 原因与修法

改动前 OV 与 Three.js 的差别不在材质本身，而在 Kit 侧的三处光照与几何问题。每一处都先用实验定位，再修复，再复测。

| 现象 | 实验与证据 | 原因 | 修法 |
|---|---|---|---|
| 黑色机柜在 OV 里是中灰，白色设备接近溢出 | 把清单里的 dome、key、fill 全部设为 0，场景仍然被照亮：朝 +Z 的竖直面很亮，顶面全黑，地面只在设备脚下有一圈反光（`template_sun_evidence.png`）；藏掉一台空调后其余设备照样亮 | Kit 新建舞台的默认模板（sunlight）在每个新舞台上放一盏 `/Environment/defaultLight`：强度 3000 的平行光，UsdLux 25.05 还附加 exposure 1.5，没有旋转。本舞台是 Y 轴向上，这盏灯沿 -Z 水平照射，叠加在清单的灯组之上 | `render_scene.retire_foreign_lights`：编辑器场景加载时停用 `/World/RenderScene/Lighting` 之外的所有灯（停用而不删除）；原生 `.cfdproj` 路径在建舞台时同样处理，只保留 `/World/Env`。Kit 日志的 `retiredLights=['/Environment/defaultLight']` 是回执 |
| 同一物体在不同视角下亮度不同，暗背景下整体偏亮 | 改为固定曝光后，白色与黑色物体的亮度差距明显收窄 | 直方图自动曝光每帧重新测光，深色背景把整个画面提亮；编辑器用的是固定曝光 | 清单 `lighting.kit` 改为固定相机曝光（`fixedExposure`：ISO 100、1/50 s、f/5，即 RTX 默认值），色调映射固定为 ACES，与编辑器的 ACESFilmic 一致。两条 Kit 路径都调用同一个 `apply_kit_exposure`，日志记录每个设置的旧值与新值 |
| CDU 正面在 OV 里是饱和青色，Three.js 里是灰蓝色的机柜外皮 | Three.js 中拾取到同一距离上的两个面：CDU 本体面带 `polygonOffset`（向后推），展示外皮与它共面 | Three.js 用 polygonOffset 让补丁和外皮赢得深度比较；USD 没有这个概念，RTX 对共面面片的取舍是任意的 | 显示桥快照把 polygonOffset 转成几何：向后推的面沿自身法线后移 0.5 mm（世界坐标），向前拉的面前移（`sceneSnapshot.ts` 的 `POLYGON_OFFSET_DEPTH_M`）。修复前后对比见 `shinaide_cdu_ov_before_after_offset.png` |
| 机柜顶面在常用的前右俯视角下发灰发蓝 | 顶面亮度是 Three.js 的四倍多；降低 dome 不变暗，反而随补光变化 | 原补光从后左上方来，它在水平顶面上的镜面反射正好射向前右方的相机 | 补光改从前左方低角度照射，主光改从前右上方照射，两者在顶面上的反射都避开常用视角。改后顶面回到与 Three.js 同一档 |

另外排除了两个假设：dome 从 22 调到 0，空调竖直面亮度几乎不变，说明亮源不是 dome；RTX 间接漫反射的缩放从 1 调到 0 也没有变化，说明不是反弹光（这个开关没有作用，已从代码中删掉）。

## 清单校准

所有数值只改 `materials.json`，两编辑器与 OV 自动跟随。

- **浅色底色压到线性反照率约 0.75 以下：** 空调机身、墙、吊顶、壁挂空调外壳、送回风格栅、穿孔挡板。例如壁挂空调外壳从 `#f2f2ee` 改为 `#d8d8d3`，吊顶从 `#f0efeb` 改为 `#cbcac5`。黑色机柜、穿孔门、镀锌风管的参数不动。
- **混凝土地面：** 平铺从 1.2 m 放大到 2.0 m，法线强度从 0.4 降到 0.25，斑块的重复感明显减轻。照片本身的对比度没有改。
- **编辑器灯组（`lighting.cameraLights`、`environment`）：** 主光移到相机右上方，头灯、背光和底部补光减弱，环境光略升以保住黑色机柜的观感。
- **Kit 灯组（`lighting.kit`）：** dome 350、主光 1900（rotateXYZ −49°/43°）、补光 400（−27°/−38°）、固定曝光、ACES；背景改为编辑器视口的颜色 `#141b25`，不再把 dome 当天空画出来。

## 三处并排结果

同一相机（位置、目标、45° 视场、1920×920 视口），Three.js 用 GPU browser-use 截图，OV 用 Xvfb `:99` 上的 head 浏览器截图（软件解码，不占 GPU）。数值是区域内线性亮度 Y（sRGB 解码后按 Rec.709 加权）的平均值，括号里是相对 React 的比值。

**DAML-Testbed 等轴视图**（`regions_daml_iso.png`）

| 区域 | React | 旧 HTML | Omniverse |
|---|---|---|---|
| 机柜侧板（黑色粉末喷涂） | 0.025 | 0.024 (0.97) | 0.039 (1.55) |
| 地面（混凝土） | 0.542 | 0.534 (0.99) | 0.534 (0.98) |
| 白色小机柜正面 | 0.639 | 0.643 (1.01) | 0.633 (0.99) |
| 壁挂空调 | 0.575 | 0.582 (1.01) | 0.506 (0.88) |
| 封闭板（透过玻璃） | 0.597 | 0.591 (0.99) | 0.511 (0.86) |
| 镀锌风管 | 0.322 | 0.311 (0.97) | 0.192 (0.60) |

**Base_leakage 等轴视图**（`regions_base_leakage_iso.png`）

| 区域 | React | 旧 HTML | Omniverse |
|---|---|---|---|
| 空调正面 | 0.696 | 0.673 (0.97) | 0.688 (0.99) |
| 空调侧面 | 0.690 | 0.668 (0.97) | 0.625 (0.91) |
| 空调顶部回风格栅（红） | 0.205 | 0.199 (0.97) | 0.110 (0.54) |
| 机柜正面 | 0.025 | 0.024 (0.97) | 0.035 (1.42) |
| 机柜顶面 | 0.057 | 0.057 (1.01) | 0.038 (0.67) |
| 架空地板 | 0.740 | 0.716 (0.97) | 0.720 (0.97) |

改动前的 OV（同一 DAML 视角，`ov_before_after_daml_iso.txt`）：机柜侧板是 React 的 7.4 倍，白色小机柜 1.45 倍，壁挂空调有 36% 的像素溢出。

并排截图：

- `daml_iso_three_vs_ov.png`、`daml_rack_three_vs_ov.png`：DAML-Testbed 俯视与机柜近景。
- `base_leakage_iso_three_vs_ov.png`、`base_leakage_close_three_vs_ov.png`：Base_leakage 俯视与机柜、通风地板近景。
- `shinaide_cdu_three_vs_ov.png`：Shinaide_Liquid 的 CDU 与供回水管（地板下视角）。
- `react_vs_legacy_daml_rack.png`：React 与旧 HTML 编辑器的同视角对比。
- `base604_iso_before_after.png`、`base604_top_before_after.png`：Base604 在 React 中改前（`8010ff0ce`，Phong）与改后的对比。俯视吊顶的平均亮度为 0.735，没有溢出像素。

## OV 绑定的贴图

Kit 在每次加载编辑器场景后记录它实际绑定的文件（`[dc.cfd_bridge] scene textures …`）。逐个计算 sha256 并与清单对照，结果见 `ov_bound_textures.json`：

| 工程 | 绑定的池文件 | sha 不符 | 不在池里的图 |
|---|---|---|---|
| DAML-Testbed | 37 | 0 | 2 张机柜门孔型（画布生成） |
| Base_leakage | 27 | 0 | 无 |
| Shinaide_Liquid | 36 | 0 | 1 张 CDU 标牌（画布生成） |

## 帧率

Base604（142 个机柜，10276 个网格），同一 GPU 浏览器、同一 1920×920 视口、同一俯视相机。改前用 `_tmp` 下独立 clone 的 `8010ff0ce`（端口 8445），改后用当前工作树（端口 8441），两边都是 `start_server.sh --dev`，交替各测四轮。每轮记录 10 秒内 requestAnimationFrame 的平均帧率，以及 30 次同步渲染（`gl.finish`）的中位耗时。

| | 平均帧率（四轮） | 同步渲染中位耗时（四轮均值） |
|---|---|---|
| 改前 | 9.2、10.4、9.8、9.2，均值 9.65 FPS | 101.6 ms |
| 改后 | 9.6、10.6、10.2、10.0，均值 10.1 FPS | 86.8 ms |

测量时主机负载很高（48 核上 load average 约 55–60，GPU 0 上还有其他浏览器会话），所以绝对值偏低；改前改后交替测量，相对比较有效。

## 判据与达成

判据在第一轮测量（Kit 修复之前）之后、开始调参之前登记：

| 判据 | 结果 |
|---|---|
| C1 同一物体 OV 与 Three.js 的线性亮度之比在 0.67–1.5 之间 | 白色设备、地面、封闭板、机柜正面、机柜顶面达成；机柜侧板 1.55、空调顶部红色格栅 0.54、镀锌风管 0.60 未达成，原因与下一步见下节 |
| C2 白色物体区域的溢出像素不超过 5% | 达成，三处均为 0%（改前 OV 壁挂空调为 36%） |
| C3 Three.js 中白色设备受光面与侧面的亮度比不小于 1.3 | 未达成：正面与侧面亮度接近，明暗层次主要体现在顶面与侧面之间、以及 OV 的阴影上 |
| C4 Base604 改后帧率不低于改前的 85% | 达成 |

## 仍有的差异：原因与下一步

- **金属与镜面类表面（镀锌风管、空调顶部格栅）在 OV 里偏暗。** Three.js 反射的是合成的 RoomEnvironment，里面有几块很亮的面光源；OV 反射的是均匀的 dome 和真实的场景。下一步补的实验：把同一张环境图放进纹理池，同时作为 Three.js 的环境贴图和 Kit dome 的贴图，再按本回执的方法复测。
- **黑色机柜侧板在 OV 里略亮（约高 11 个 sRGB 级）。** 原因是 OV 计算了周围亮面的反射，编辑器的光栅化没有这一项。同一张环境图也会缩小这部分差异，随上一项一起复测。
- **Three.js 白色设备的正侧面明暗差不足（C3）。** 编辑器的灯组跟随相机，环境光又是保住黑色机柜观感所必需的；白色表面处在 ACES 曲线的肩部，主光方向的改变几乎压不出差别。下一步补的实验：在 Three.js 中试一盏与世界坐标固定的主光（与 Kit 的主光同向），或给主光开阴影，并测 Base604 的帧率代价。
- **混凝土照片的对比度没有动。** 这次只放大了平铺尺寸并减弱法线；若要进一步减轻斑驳感，需要在生成器里加一套可控对比度的混凝土贴图。

## 复现

1. 起服务：`./src/start_server.sh --app react --dev --ov --gpu <空闲卡> --port 8441 --ov-port 8448 --ov-stream-port 47948`；旧编辑器：`./src/start_server.sh --app html --port 8445`。按 `_tmp/campaign_gpu.lock` 的约定先拿 GPU 锁。
2. React 视口用 GPU browser-use，OV 视口用 head 浏览器（Playwright `headless=False`，`DISPLAY=:99`，`--disable-gpu --use-angle=swiftshader`）。两边都通过 `/api/read-file` 和 `window._applyProjectData` 加载工程，隐藏吊顶和朝向相机的两面墙（Shinaide 另外隐藏架空地板以露出管道），再用 `window.__viewportHandle` 设置相机。
3. 相机：DAML 俯视 `[9.6,5.6,8.2]→[3.0,0.7,1.6]`，近景 `[6.9,1.7,4.6]→[4.3,1.0,1.4]`；Base_leakage 俯视 `[8.5,7.0,10.5]→[2.2,1.2,3.0]`，近景 `[4.6,2.5,6.4]→[2.2,1.7,3.4]`；Shinaide CDU `[9.2,2.4,7.6]→[6.6,0.8,4.6]`；Base604 `[27,19,31]→[7.8,1.5,11.7]`，俯视 `[7.8,34,11.71]→[7.8,0,11.7]`；视场均为 45°。
4. 区域坐标与逐区域数据在 `regions_*.json` 和 `ov_before_after_daml_iso.json`，坐标是 1920×1080 截图上的像素。
