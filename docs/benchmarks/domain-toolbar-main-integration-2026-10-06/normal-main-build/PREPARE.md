# Normal MAIN frontend build：等待 ROOT GO

已读既有正规MAIN/IT build-and-preserve脚本、package/tsconfig/Vite以及Main6基线，只准备不执行。MAIN与IT dependencyclosure-v2提供149 controlledPASS/sourceGO，原失败完整保留。ROOT尚需完成cherry并给新MAIN HEAD；另ROOT会正规重启IT8396/8397加入4diagflags，本工作必须等待真实新IT launcher/backend PID后才建立最终6PID/5listener窗口。现在不会用旧645551/645583作验收对照。Main/Kit当前4PID只有读参考，不能据准备阶段数据说后续未变。

拟执行一次标准 `/usr/bin/npm run build`（tsc -b && vite build），cwd=B/src/frontend/react，CPU28–31/CUDA空；TMP/TEMP/TMPDIR、npm cache/log、XDGcache均指向本目录owned子目录。正规built-in产物在B内，不引外部工作路径/依赖或额外测试。

ROOT GO之后先核Main/IT HEAD/index、Main144+IT140SHA、Main6原字节及tracked flags/lock ignore，再capture6process startTicks/argv/CPU/nativeflags和5监听socket inode。旧Maindist必须完整copy+逐SHA验证到本目录dist-before；备份不可移除原树或碰服务。新build结束只append缺失旧assets中hashed文件与proof全部文件，已有同名SHA一致则记same，不同则拒绝覆盖。新index保持新build输出。无browserreload、Kitrestart、API、业务/native或Git mutation。

结束再次核源/HEAD/index、protected6/flags/process/listeners；原dist副本SHA以及所有恢复文件SHA绑定receipt。build rc非0或碰撞/源和现场漂移要明确失败，不能称正常部署或真实UI已过。只有 ROOT 后续正常browser验收才能说明新MAIN UI。准备参考脚本仅逐字copy保真，不会误运行原脚本（其常量指向旧IT或旧PID）。
