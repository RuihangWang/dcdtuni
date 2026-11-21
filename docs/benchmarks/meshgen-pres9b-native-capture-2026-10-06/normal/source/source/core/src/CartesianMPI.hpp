#pragma once
// ============================================================================
// CartesianMPI.hpp —— MPI 域分解生成（引擎内部头，仅 MESHGEN_WITH_MPI=ON 构建存在）
//
// 设计出处：docs/cartesian_dev/19_mpi_design.md v2（下称 §）
//   · §2.a 分解：Morton 升序叶表的连续段（rank r 持 [i_r, i_{r+1})）
//   · §2.b 层 1：marks 本地化 + 候选 allgather + 复制树确定性重放
//   · §2.d 界面点键：确定性重建（本里程碑 Mode G 下由 rank0 串行重放兜底）
//   · §2.f 模式 G：rank0 汇聚 + 原样重放串行后半程 ⇒ 产物与单进程逐字节同一
//   · §2.g 编译开关：MESHGEN_WITH_MPI 缺省 OFF；ON 时全部代码在 #ifdef 内
//   · §2.h R5/R11：启动握手哈希 + 每 pass 树哈希金丝雀
//
// 分层纪律（红线）：本 TU 只含【通信 + 进程 + 分布算法骨架】，不含装配知识；
// 引擎侧（Cartesian.cpp）只出现 #if defined(MESHGEN_WITH_MPI) 包裹的最小扩展点，
// OFF 构建预处理后与基线源码逐字相同 ⇒ 目标文件逐字节同一。
// ============================================================================
#if !defined(MESHGEN_WITH_MPI)
#error "CartesianMPI.hpp 只能在 MESHGEN_WITH_MPI=ON 构建中包含"
#endif

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "meshgen/Cartesian.hpp"
#include "meshgen/Vec.hpp"

namespace meshgen {
namespace mpi {

// ---------------- 进程层 ----------------
// launched()：本次进程是否由 mpirun/mpiexec 拉起（OMPI_COMM_WORLD_SIZE / PMI_SIZE /
//   PMIX_RANK / SLURM_NTASKS 任一存在）。MESHGEN_MPI=0 强制否、=1 强制是。
// init()：launched() 为真时 MPI_Init_thread(FUNNELED)（引擎工作线程不触 MPI，
//   全部 MPI 调用收敛主线程，§2.b 末）；否则不初始化 ⇒ active()=false ⇒ 引擎走
//   原串行路径（ON 构建的直接执行与 OFF 构建行为逐字节一致）。
bool launched();
bool init(std::string& err);   // 幂等；失败给出诚实错误
void shutdown();               // MPI_Finalize（若已初始化）
bool active();                 // 分布路径生效（已初始化）
int rank();                    // 未初始化 ⇒ 0
int size();                    // 未初始化 ⇒ 1
bool root();                   // rank()==0（未初始化 ⇒ true）
void barrier();

// 全秩一致退出码：rank0 的 rc 广播为所有秩的 rc（避免部分秩 0 退出、部分秩 1）。
// 模式 G 分工点之后（enterRootPhase 已调用）自动退化为恒等：非 rank0 已 Finalize 退出，
// 再广播会挂死；此时 rank0 的 rc 由 mpirun 传播（实测 Open MPI 5.0.10：任一秩非零
// ⇒ mpirun 返回该码，见 19_mpi_design.md 附录 C 失败模式表）。
int consensusExit(int rc);

// ---------------- 模式 G 分工点（§2.f） ----------------
// 逐叶场（hitTris/solid）分布计算完成后，装配后半程（uf-flood → S8/S9/S10/S11 →
// AMR 场 → 导出）是 rank0 的串行重放段。此函数由【全部秩】在同一处调用：
//   · rank0：把线程池扩到 machineThreads()（其余秩即将退出 ⇒ 不再超订阅），返回 true 继续；
//   · 非 rank0：返回 false（调用方立即 return，CLI 跳过导出，main 走 shutdown 退出 0）。
// 非 rank0 不等待、不轮询、不进任何集合通信 ⇒ 不烧 rank0 的核，也无死锁面。
// 线程数上限可用 MESHGEN_MPI_ROOT_THREADS 覆盖（W2/F3 修复后缺省 =
// min(hardware_concurrency, 进程亲和掩码核数)——taskset/cgroup 下不再超订阅抖动；
// macOS 无亲和接口 ⇒ 缺省 = hardware_concurrency，历史行为不变）。
bool enterRootPhase();
size_t machineThreads();

// ---------------- 诊断/测试开关 ----------------
// 挂起分布路径（active() 临时返回 false）⇒ 同进程内跑一次【串行参考实现】，
// 供 tests/test_cartesian_mpi.cpp 做 MPI-vs-串行 逐位 A/B 对照（不依赖外部基线文件）。
// 契约：挂起期间引擎不发起任何集合通信 ⇒ 各秩可独立跑串行参考；调用方须保证
// 挂起/恢复成对出现且不在集合通信中途切换。生产路径不调用。
void setSuspended(bool s);
bool suspended();

// 全秩一致失败判定：任一秩带非空 err ⇒ 所有秩拿到同一 err（rank 最小者优先），
// 返回 true。用于集合通信前后的一致性（§2.e "报错路径 = 全秩同 err 返回"）。
bool anyFail(const std::string& localErr, std::string& globalErr);

// ---------------- 分解（§2.a） ----------------
// 连续段分解：n 个元素（Morton 升序叶表 / 全局胞表）按秩等分，余数前置
// （rank r 段长 = n/P + (r < n%P ? 1 : 0)）。各秩独立计算，无需通信。
void range(long long n, long long& lo, long long& hi);
// 指定秩的段（用于 allgatherv 的 recvcounts/displs）
void rangeOf(int r, long long n, long long& lo, long long& hi);

// 线程池区间执行的秩段版：把 [lo,hi) 交给本进程线程池（段内升序、逐元素独立）。
// 串行等价：lo=0,hi=n 时与 globalPool().parallelRanges(n, body) 逐元素同序。
// 注意：本原语【不受】段限域钩子影响（内部临时摘钩再执行）——它自己就是显式区间。
void parallelRanges(long long lo, long long hi,
                    const std::function<void(size_t, size_t)>& body);

// ---------------- E2（M1b 装配尾分布化）：段限域钩子 ----------------
// poolSetSegment(lo,hi)：此后 globalPool().parallelRanges(n, body) 只执行
// [lo,hi)∩[0,n)，任务边界对齐全局 chunk 网格（见 ThreadPool.hpp 注释）。
// 只允许在装配尾分布段内由主线程设置；引擎侧用 RAII 守卫保证异常/错误路径清除。
void poolSetSegment(long long lo, long long hi, int virtMul);
void poolSetSegmentExtras(std::vector<size_t> ex);   // 附加单点任务（仅 fails[ci] 类 body）
void poolClearSegment();
bool poolSegmentActive();

// ---------------- 集合通信薄层 ----------------
// 候选键（全局叶号，本秩升序）→ rank 序拼接的全局升序表（§2.b 层 1）。
// 各秩段互不重叠且按秩升序 ⇒ 拼接结果 = 串行扫描 [0,n) 的候选表，逐元素同一。
void allgatherLL(std::vector<long long>& v);
// 逐叶 int8 场：本秩只填 [lo,hi)，其余位置由属主秩补齐（v 尺寸 = 全量 n）。
// W1(F1) 后走 MPI_IN_PLACE：lo/hi 必须 == range()/rangeOf() 对本秩的等分结果
// （= recvbuf+dsp[rank] 前提；违例 ⇒ 诚实中止，MPICH 别名 Abort 的标准修法）。
void allgatherI8(std::vector<int8_t>& v, long long lo, long long hi);
// 逐叶变长 int 表（hitTris）：本秩只填 [lo,hi)（v 尺寸 = 全量 n，条目已清空）。
void allgatherLists(std::vector<std::vector<int>>& v, long long lo, long long hi);
// 逐叶 int8 提案表（洪泛 halo 轮）：本秩提交 (全局叶号, 值) 提案，全秩收到全部提案。
void allgatherProposals(std::vector<std::pair<long long, int8_t>>& mine,
                        std::vector<std::pair<long long, int8_t>>& all);
// (全局叶号, 整数距离) 提案表（S5 分布波的桶锁步跨界松弛，§2.b 层2）：
// 全秩收到全部提案（rank 序拼接；接收侧按 CAS 取 min，与到达序无关）。
void allgatherLLPairs(std::vector<std::pair<long long, long long>>& mine,
                      std::vector<std::pair<long long, long long>>& all);
long long allreduceSum(long long v);
long long allreduceMax(long long v);
void bcastString(std::string& s);   // rank0 → 全秩（错误串/退出码用）

// ---------------- E2（M1b 装配尾分布化）新增薄层 ----------------
// 逐胞 uint8 场：本秩只填 [lo,hi)，其余由属主秩补齐（v 尺寸 = 全量 n）。
// W1(F1) 后走 MPI_IN_PLACE（前提同 allgatherI8）。
void allgatherU8(std::vector<uint8_t>& v, long long lo, long long hi);
// 同上，但段界【非等分】（胞段 = kept 前缀和像）：段长先行 allgather，
// 位移 = 秩主序前缀和（必须与 lo 一致，否则诚实中止）。
void allgatherU8V(std::vector<uint8_t>& v, long long lo, long long hi);
// 变长字节 blob：allgatherv 按 rank 序拼接（out = 全秩 mine 依秩串接）。
void allgatherBytes(const std::vector<char>& mine, std::vector<char>& out);
// 变长字节 blob：只汇聚到 rank0（outRoot 仅 rank0 有效；全秩都必须调用）。
void gatherBytesToRoot(const std::vector<char>& mine, std::vector<char>& outRoot);
// 变长字节 blob：root 秩广播到全秩（buf 在非 root 秩被覆盖；长度先行）。
// lockstep delta 用：root 动态（= 当前操作的属主秩）。
void bcastBytes(std::vector<char>& buf, int root);
bool allreduceOr(bool v);

// ---------------- F2（M1b 秩失步修复）：dist 尾 run-end 状态同步 ----------------
// dist 尾在 S11 前分工：非 rank0 释放内存后立即返回，rank0 独自重放 S11→导出准备。
// 若 rank0 尾段失败（如 S11 界面配对拒出），非 rank0 已带 ok=true 走人 ⇒ 秩失步 ⇒
// 调用方（测试/后续集合通信）错配挂死（27 §2 实证）。修复口径 = 失败即全秩同步：
// 分工点置 engaged；generateCartesian 末尾全秩调 syncFinalStatus——rank0 广播
// (ok, err)，非 rank0 阻塞接收并以 rank0 的结果覆盖自身 ⇒ 全秩同 ok/err 返回。
// 非 rank0 的等待发生在内存释放之后、MPI_Finalize（本就是集合点）之前 ⇒ 墙钟零损失。
void setDistEngaged(bool v);
bool distEngaged();
void syncFinalStatus(bool& ok, std::string& err);   // 全秩同调；仅 engaged 后有效

// ---------------- M1b 收尾③：dist 尾模式实果标签（CLI "分布=" 打印用） ----------------
// 进程级状态，每次 assembleMesh 分工点重置为默认 "模式G(rank0 汇聚重放)"（与旧硬编码
// 打印逐字一致 ⇒ 默认路径 stdout 零变化）；DISTTAIL=1 时按实际结局改写：真开 =
// G-D；被 mode2d / 诊断消融 env / S8-A2 重建胞中途回退拦下 = 带真实原因的模式G。
// 只存静态字符串字面量，无所有权问题。
void setDistTailMode(const char* label);
const char* distTailMode();
// 跨秩独占前缀和：返回 Σ_{s<rank} v（各秩同调，P 次小通信）。
long long scanExclusiveLL(long long v);
// 物理内存 MB（内存守卫用；获取失败返回 0 ⇒ 调用方按"未知"处理）。
long long physMemMB();

// ---------------- 握手与金丝雀（§2.h R5/R11） ----------------
// 启动握手：域参数 (origin,H,base,maxLv,Ls) + 表面指纹（顶点数/三角形数/坐标位模式
// FNV-1a）+ 叶数，按 double 位模式做 allreduce(min)/allreduce(max) 一致性校验；
// 不一致 ⇒ 拒跑（异构构建 fp 漂移、配置不一致、STL 版本不一致的早期捕获）。
bool handshake(const Vec3& origin, double H, int base, int maxLv, int Ls,
               long long nLeaves, const TriSurface& surf, std::string& err);
// 树哈希金丝雀：leafKeys_ + 叶级 的 FNV-1a 64 位摘要，全秩比对（不一致 ⇒ 树已发散，
// 立即拒跑而不是让后续集合通信错位/产物静默不同）。cost ≈ 一次线性扫（12B/叶）。
bool treeCanary(const CartesianMesh& m, const char* where, std::string& err);
// 金丝雀硬失败版：不一致 ⇒ rank0 打印诊断 + MPI_Abort（发散树继续跑只会在后续集合
// 通信错位/挂死或产出静默错误的网格，两者都不可接受；与引擎既有"响应失败不静默"
// 的硬断言同风格，如 CART_CHECKNBR / S10 闭合终检）。
void treeCanaryAbort(const CartesianMesh& m, const char* where);

// ---------------- 分布算法（引擎回调 + 公共网格 API） ----------------
// computeLeafSolidity 的分布版（§2.b 层 2 骨架的"固体侧洪泛"实例）：
//   各秩对本地叶段做 BFS 洪泛（值由 evalSolid 给出：叶心绕数判定，与串行同一函数）
//   → 跨界邻居提案 allgather → 应用后继续洪泛 → allreduce(changed) 静默判；
//   轮数上界 = P（每轮波前至少跨一个秩边界一次），硬帽 64 轮超限报错（§2.b 口径）。
// 结果与串行逐位同一的论证：solid 的取值是"叶所属空叶连通分量"的函数（相邻空叶不可能
// 被表面分隔——被表面穿过的叶必为切割叶），故洪泛合流（confluent），与访问序无关。
// evalSolid(leaf, centre) → 1（固体）/0（流体）；由引擎传入 pointInSolid 同一实现。
bool leafSolidityDistributed(long long n, const CartesianMesh& mesh,
                             const std::vector<std::vector<int>>& hitTris,
                             const std::function<int8_t(long long, const Vec3&)>& evalSolid,
                             std::vector<int8_t>& solid, std::string& err);

// ---------------- 计时/诊断 ----------------
double wtick();                     // MPI_Wtime（未初始化 ⇒ steady_clock 秒）
void logRoot(const std::string& s); // rank0 stderr 一行（[mpi] 前缀）

}  // namespace mpi
}  // namespace meshgen
