# 热点生成与消除动画

项目主页上那段「热点出现 → 改变送风 → 热点消除」的双动画，从 CFD 求解到成片的完整链路。
两个场景：停三台空调（整屋升温）与停一台（局部热区）。

## 成片

| 场景 | 位置 | 时长 |
|---|---|---|
| 三台故障 | `case/GTC/forOVdemo/hotspot_video/` | 10.63 s |
| 单台故障 | `case/GTC/forOVdemo/hotspot_1acu/video/` | 10.63 s |

每个场景各 4 个文件：`hotspot_thermal.mp4/.gif`（温度场）与 `hotspot_flow.mp4/.gif`（气流组织）。
主页用的两个 mp4 另外拷贝在 `project_page/public/assets/`，那两份是仓库里唯一被跟踪的视频。

## 两个场景的数值

房间 31 × 19.2 × 5 m，50 台机柜，6 台空调，装机制冷 402.8 kW。

| | 三台故障（m_51 / m_52 / m_54） | 单台故障（m_54） |
|---|---|---|
| 制冷损失 | 207.8 kW（52%） | 67.9 kW（17%） |
| 全场平均升温 | +9.97 K | +2.13 K |
| 99 分位升温 | +19.91 K | +7.75 K |
| 峰值温度 | 44.4 → **54.3** °C | 44.4 → **45.9** °C |
| 干预后峰值 | 41.2 °C（低于基线） | 36.3 °C |
| 色标域 | 15.0 – 43.6 °C | 15.0 – 35.4 °C |

干预动作两场景相同：在役机组风量 ×1.8、送风温度降到 15 °C。

**两个场景的色标域不同，不要按颜色跨场景对比**，要看数值。单台场景的 S0 直接复用三台场景那次
求解 —— 同一工况、同一网格，重算只会引入无意义的差异。

## 链路

```
hotspot_anchors.py   三个稳态工况，一套网格（S0 正常 / S1 故障 / S2 干预）
        ↓            走服务器 API 的 solveOnly，网格指纹前后比对
hotspot_frames.py    foamToVTK → 逐点过渡 → 31 个 .vtu + frames.json
        ↓            同时写 point 与 cell 两种关联
hotspot_record.py    真头浏览器驱动 Omniverse 视口，逐帧抓温度场与流线
        ↓            就绪判据是画面本身，不是后端日志
hotspot_assemble.py  叠加字幕/色标/最热区/ACU 标记 → mp4 + gif
```

`hotspot_acu_positions.py` 单独跑一次，测出六台空调在画面里的像素位置，供合成时画标记。

## 重新生成

```bash
R=$(git rev-parse --show-toplevel)
S=$R/src/frontend/omniverse/cfd_demo/scripts

# 求解服务器：hotspot_anchors.py 默认连 STLGEN_SERVER=http://localhost:8005，
# 并用脚本里写的管理员账号登录，所以用同一组账号经环境变量启动这台服务器
DCDTUNI_ADMIN_USER=… DCDTUNI_ADMIN_PASSWORD=… \
    ./src/start_server.sh --port 8005 --case-dir $R/case/GTC
# Kit + 参考界面：录制脚本默认打开 http://localhost:8081
./src/start_server.sh --app omniverse --port 8081
KITLOG=$(ls -d $R/.codex-tmp/ov-runtime/8301.* | tail -1)/kit.log   # 本次 Kit 的日志

conda run -n dcdtuni python $S/hotspot_anchors.py     # 三次稳态求解，约 15 分钟
conda run -n dcdtuni python $S/hotspot_frames.py      # 31 帧，约 5 分钟
DISPLAY=:99 KITLOG=$KITLOG HOTSPOT_FLOW_NO_VOLUME=1 python3 $S/hotspot_record.py  # 录制，约 35 分钟
conda run -n dcdtuni python $S/hotspot_assemble.py    # 合成，约 8 分钟
```

> 2026-10-21 注：成片做于 2026-08-18，当时 Kit 由 `start_demo.sh` 直接启动、默认用另一张卡、日志在
> `/tmp/dc_cfd_demo.kit.log`。现在一律经 `src/start_server.sh` 启动（默认 GPU 0，日志在
> `.codex-tmp/ov-runtime/<信令端口>.*/kit.log`），所以录制时要用 `KITLOG` 指到本次 Kit 的日志；
> 启动器默认端口是 8000，两台服务都要显式写 `--port`。上面的命令按现行启动方式改写，尚未整条重跑；
> 下一步是按这套命令重跑一次单台故障场景，确认成片一致。

单台故障场景把上面四步换成：

```bash
A=$R/case/GTC/forOVdemo/hotspot_1acu
HOTSPOT_DEAD=m_54 HOTSPOT_OUT=$A/anchors  … $S/hotspot_anchors.py S1 S2
HOTSPOT_OUT=$A/anchors HOTSPOT_FRAMES=$A/frames                    … $S/hotspot_frames.py
HOTSPOT_FRAMES=$A/frames HOTSPOT_SHOTS=$A/shots                    … $S/hotspot_record.py
HOTSPOT_FRAMES=$A/frames HOTSPOT_SHOTS=$A/shots HOTSPOT_VIDEO=$A/video … $S/hotspot_assemble.py
```

中间产物（锚点解、31 帧、逐帧截图）单场景约 3 GB，两场景 6.6 GB，全部落在
`case/GTC/forOVdemo/` 下，`.gitignore` 已排除。

## 三个坑，都是查了很久才定位的

**体渲染比后端日志晚约 15 秒才进画面。** 后端打出 `volume rebuilt … visible=True` 时
IndeX 还没画完，按日志截图会拍到**上一帧的场** —— 第一轮 31 帧全是裸几何，修了阈值后又变成
31 帧同一个场。现在的判据是画面本身：先等旧体消失（回到"仅几何"参考帧），再等新体出现。
见 `hotspot_record.py:wait_until_changed` / `wait_until_similar`。

**帧文件必须带 cell 数组。** foamToVTK 同时写 point 与 cell 两种关联，kit-cae 的体素化读
cell 那份；只有 point 数据的帧会加载成功、报 visible=true、然后什么都不画。

**ACU 位置不能靠肉眼投影。** 单向通道拿不到相机矩阵，所以用"隐藏一台→截图→作差"来测。
注意作差要取变化**最集中**的那一块：隐藏一台空调会让全屋光追照明轻微变化，低阈值下几万个弱
变化像素会把质心拖到画面中央，六台"定位"到同一处。校验是几何本身给的 —— 六个结果落成两条
平行斜线（每面墙一条），近处机组的像素数是远处的 5 倍。

## 标注的口径

温度场那段：色标带数值，域是钉死的，所以同一颜色在任意时刻对应同一温度；画面上圈出的
「最热区」是**屏幕上最热的一片**（色标单调，最红的像素就是视线上最热的），标签温度取该帧场的
真实峰值。故障相里整屋都在高温，并不存在孤立热点，圈的是区域而非某个单元。

流线那段：体渲染是隐藏的，色标只标「冷/热」不带数值 —— 流线着色走的是自适应标度的
ScalarColor 材质，逐帧重新拟合，**颜色不跨帧可比**，可比的是流线形态。
