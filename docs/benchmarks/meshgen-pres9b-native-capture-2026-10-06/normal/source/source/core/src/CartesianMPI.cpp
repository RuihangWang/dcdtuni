// ============================================================================
// CartesianMPI.cpp —— MPI 域分解生成：通信薄层 + 进程层 + 分布算法骨架
// 设计出处：docs/cartesian_dev/19_mpi_design.md v2 §2（M1a，模式 G）
//
// 纪律（§2.g）：本 TU 只在 MESHGEN_WITH_MPI=ON 时进构建；OFF 构建不含任何 MPI 符号，
// 引擎/CLI 中的 #if defined(MESHGEN_WITH_MPI) 块整体消失 ⇒ OFF 产物与基线逐字节同一。
//
// 字节同一策略（模式 G，§2.f）：
//   ① 树（S0-S6）全秩复制：marks 只算本秩叶段，候选 allgather 成【全局升序同一表】，
//      budgetTruncate/refineBatch 各秩同输入同算法重放 ⇒ 树逐位同一（§2.b 层 1）；
//   ② 逐叶场（hitTris/solidity）分布计算 + 全量 allgather ⇒ 与串行逐位同一；
//   ③ 装配后半程（S7 uf-flood 起 → S11c → AMR 场）由 rank0 【原样串行重放】，
//      输入序 = 全局胞升序 ⇒ 产物与单进程逐字节同一（无重排、无近似）。
//   非 rank0 在 ③ 之前返回（out 为空），由 CLI 侧跳过导出。
// ============================================================================
// W2(F3)：sched_getaffinity/CPU_COUNT 为 GNU 扩展（g++/clang++-on-Linux 均预定义
// _GNU_SOURCE，此处为其它编译器的防御性兜底；必须早于任何系统头）。
#if !defined(__APPLE__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE 1
#endif

#include "CartesianMPI.hpp"

#include <mpi.h>

#include <algorithm>
#include <chrono>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#if defined(__APPLE__)
#include <sys/sysctl.h>
#include <unistd.h>     // dc17-robust 1.10：environ
#else
#include <sched.h>      // W2(F3)：sched_getaffinity / cpu_set_t / CPU_COUNT
#include <unistd.h>
#endif

#include "meshgen/ThreadPool.hpp"

// dc17-robust 1.10：environ 的防御性声明（glibc 在 _GNU_SOURCE 下已由 <unistd.h> 声明，
// 本 TU 顶部已定义 _GNU_SOURCE；此声明仅为其它 libc 兜底，与 C 链接的既有声明兼容）。
extern "C" { extern char** environ; }

namespace meshgen {
namespace mpi {
namespace {

int g_rank = 0;
int g_size = 1;
bool g_inited = false;      // MPI_Init 已成功
bool g_active = false;      // 分布路径生效（= g_inited）
bool g_tried = false;       // init 已尝试过（幂等）
bool g_split = false;       // 模式 G 分工点已过（非 rank0 退出 ⇒ 禁再用集合通信）
bool g_suspended = false;   // 诊断/测试：临时挂起分布路径（串行参考 A/B 对照用）
bool g_distEngaged = false; // F2：dist 尾已过分工点（run-end 状态同步生效中）
// M1b③：dist 尾模式实果标签（CLI 打印用；只存静态字面量。默认串 = 旧硬编码打印
// 的 "模式G(rank0 汇聚重放)" 逐字同一 ⇒ 默认路径 stdout 零变化）
const char* g_distTailMode = "模式G(rank0 汇聚重放)";
long long g_segLo = 0, g_segHi = 0;  // E2：段限域钩子的当前段（摘钩恢复用）
int g_segMul = 1;

// 未初始化 MPI 时的秩/规模（MESHGEN_MPI=0 但由 mpirun 拉起的场景）：读启动器环境。
// 用途：CLI 的 rank0 守卫（打印/落盘）在这种情况下仍然只由 0 号进程执行——否则
// P 个进程各自跑一遍完整串行流水并【同写一个导出目录】⇒ 产物互相覆写（静默损坏）。
int envRank() {
    static const int v = [] {
        static const char* const kKeys[] = {"OMPI_COMM_WORLD_RANK", "PMI_RANK", "PMIX_RANK",
                                            "SLURM_PROCID", "MPI_LOCALRANKID"};
        for (const char* k : kKeys) {
            const char* e = std::getenv(k);
            if (e && *e) {
                const long r = std::strtol(e, nullptr, 10);
                return (r >= 0) ? (int)r : 0;
            }
        }
        return 0;
    }();
    return v;
}
int envSize() {
    static const int v = [] {
        static const char* const kKeys[] = {"OMPI_COMM_WORLD_SIZE", "PMI_SIZE", "SLURM_NTASKS",
                                            "MPI_LOCALNRANKS"};
        for (const char* k : kKeys) {
            const char* e = std::getenv(k);
            if (e && *e) {
                const long n = std::strtol(e, nullptr, 10);
                return (n >= 1) ? (int)n : 1;
            }
        }
        return 1;
    }();
    return v;
}

// FNV-1a 64：位模式摘要（握手/金丝雀用；不用 std::hash——跨实现/跨版本不保证一致）
uint64_t fnv64(const void* p, size_t n, uint64_t h = 0xcbf29ce484222325ull) {
    const unsigned char* b = (const unsigned char*)p;
    for (size_t i = 0; i < n; ++i) {
        h ^= b[i];
        h *= 0x100000001b3ull;
    }
    return h;
}
uint64_t bits(double d) {
    uint64_t u = 0;
    std::memcpy(&u, &d, sizeof(u));
    return u;
}
uint64_t mix(uint64_t h, uint64_t v) { return fnv64(&v, sizeof(v), h); }

// 集合通信硬失败：不做静默降级（半通信状态下的产物必然错）。
[[noreturn]] void commFatal(const char* what, int rc) {
    std::fprintf(stderr, "[mpi] FATAL %s: MPI rc=%d（全秩中止，避免半通信产物）\n", what, rc);
    std::fflush(stderr);
    MPI_Abort(MPI_COMM_WORLD, 90);
    std::abort();
}

void chk(int rc, const char* what) {
    if (rc != MPI_SUCCESS) commFatal(what, rc);
}

// allgatherv 的段长/位移（int 口径；超 INT_MAX 诚实拒跑）
struct Seg {
    std::vector<int> cnt, dsp;
    int total = 0;
};
Seg segments(long long n) {
    Seg s;
    const int P = g_size;
    s.cnt.resize((size_t)P);
    s.dsp.resize((size_t)P);
    long long acc = 0;
    for (int r = 0; r < P; ++r) {
        long long lo = 0, hi = 0;
        rangeOf(r, n, lo, hi);
        if (hi - lo > (long long)INT_MAX) {
            std::fprintf(stderr, "[mpi] FATAL 段长 %lld 超 MPI int 上限（需分块 allgatherv）\n",
                         hi - lo);
            MPI_Abort(MPI_COMM_WORLD, 91);
            std::abort();
        }
        s.cnt[(size_t)r] = (int)(hi - lo);
        s.dsp[(size_t)r] = (int)acc;
        acc += hi - lo;
    }
    if (acc > (long long)INT_MAX) {
        std::fprintf(stderr, "[mpi] FATAL 全量 %lld 超 MPI int 上限\n", acc);
        MPI_Abort(MPI_COMM_WORLD, 92);
        std::abort();
    }
    s.total = (int)acc;
    return s;
}

bool envFlag(const char* k, bool dflt) {
    const char* e = std::getenv(k);
    if (!e || !*e) return dflt;
    return !(std::strcmp(e, "0") == 0 || std::strcmp(e, "false") == 0);
}

// 定长上限（错误串/诊断串）：4KiB 足够定位，且杜绝 INT_MAX 级广播
constexpr int kStrCap = 4096;

// 变长 allgatherv 通用件（rank 序拼接 = 各秩段升序时的全局升序，§2.b 层 1 的口径）
template <class T>
void allgatherFlat(const std::vector<T>& mine, std::vector<T>& out, MPI_Datatype dt,
                   const char* what) {
    if (mine.size() > (size_t)INT_MAX) commFatal(what, -1);
    const int m = (int)mine.size();
    std::vector<int> cnt((size_t)g_size, 0), dsp((size_t)g_size, 0);
    chk(MPI_Allgather(&m, 1, MPI_INT, cnt.data(), 1, MPI_INT, MPI_COMM_WORLD), what);
    long long tot = 0;
    for (int r = 0; r < g_size; ++r) {
        if (tot > (long long)INT_MAX) commFatal(what, -1);
        dsp[(size_t)r] = (int)tot;
        tot += cnt[(size_t)r];
    }
    if (tot > (long long)INT_MAX) commFatal(what, -1);
    out.assign((size_t)tot, T{});
    chk(MPI_Allgatherv(m ? mine.data() : nullptr, m, dt, tot ? out.data() : nullptr,
                       cnt.data(), dsp.data(), dt, MPI_COMM_WORLD),
        what);
}

// src 秩的串广播到全秩（含 src 自身；非 src 秩的原值被覆盖）
void bcastStringFrom(int src, std::string& s) {
    if (!active() || g_size == 1) return;
    int n = (int)std::min<size_t>(s.size(), (size_t)kStrCap);
    if (g_rank != src) n = 0;
    chk(MPI_Bcast(&n, 1, MPI_INT, src, MPI_COMM_WORLD), "Bcast(strLen)");
    s.resize((size_t)n);
    if (n > 0) chk(MPI_Bcast(&s[0], n, MPI_CHAR, src, MPI_COMM_WORLD), "Bcast(str)");
}

}  // namespace

// ---------------- 进程层 ----------------
bool launched() {
    static const int v = [] {
        const char* force = std::getenv("MESHGEN_MPI");
        if (force && *force) return (std::strcmp(force, "0") == 0) ? 0 : 1;
        // Open MPI（mpirun/mpiexec）注入的通用环境；PMI/PMIx/SLURM 作为兜底探测
        static const char* const kKeys[] = {"OMPI_COMM_WORLD_SIZE", "PMI_SIZE", "PMIX_RANK",
                                            "SLURM_NTASKS", "MPI_LOCALNRANKS"};
        for (const char* k : kKeys)
            if (std::getenv(k) != nullptr) return 1;
        return 0;
    }();
    return v != 0;
}

bool init(std::string& err) {
    err.clear();
    if (g_tried) return g_active;
    g_tried = true;
    if (!launched()) return false;   // 直接执行（非 mpirun）⇒ 引擎走原串行路径
    int flag = 0;
    if (MPI_Initialized(&flag) != MPI_SUCCESS || flag) {
        g_inited = true;
    } else {
        // 引擎工作线程不调用 MPI（全部 MPI 调用收敛主线程）⇒ FUNNELED 足够（§2.b 末）
        int provided = MPI_THREAD_SINGLE;
        const int rc = MPI_Init_thread(nullptr, nullptr, MPI_THREAD_FUNNELED, &provided);
        if (rc != MPI_SUCCESS) {
            err = "MPI_Init_thread 失败 rc=" + std::to_string(rc);
            return false;
        }
        if (provided < MPI_THREAD_FUNNELED) {
            err = "MPI 线程支持不足（需 FUNNELED，实得 " + std::to_string(provided) + "）";
            MPI_Finalize();
            return false;
        }
        g_inited = true;
    }
    chk(MPI_Comm_rank(MPI_COMM_WORLD, &g_rank), "Comm_rank");
    chk(MPI_Comm_size(MPI_COMM_WORLD, &g_size), "Comm_size");
    g_active = g_inited;
    if (g_size < 1) {
        err = "MPI_Comm_size 返回非法值 " + std::to_string(g_size);
        g_active = false;
        return false;
    }
    return g_active;
}

void shutdown() {
    if (!g_inited) return;
    int flag = 0;
    if (MPI_Finalized(&flag) == MPI_SUCCESS && !flag) MPI_Finalize();
    g_inited = false;
    g_active = false;
}

bool active() { return g_active && !g_suspended; }

void setSuspended(bool s) { g_suspended = s; }
bool suspended() { return g_suspended; }
// 未初始化（含 MESHGEN_MPI=0 强制关闭）时回落启动器环境 ⇒ CLI 的 rank0 守卫
// （打印/落盘）在"mpirun 拉起但分布路径关闭"下仍然只让 0 号进程落盘，杜绝 P 份
// 进程同写一个导出目录的静默覆写。
int rank() { return g_inited ? g_rank : envRank(); }
int size() { return g_inited ? g_size : envSize(); }
bool root() { return rank() == 0; }

void barrier() {
    if (!active() || g_split) return;   // 分工点后非 rank0 已退出 ⇒ 任何集合通信都会挂死
    chk(MPI_Barrier(MPI_COMM_WORLD), "Barrier");
}

int consensusExit(int rc) {
    if (!active() || g_split) return rc;
    int out = rc;
    chk(MPI_Bcast(&out, 1, MPI_INT, 0, MPI_COMM_WORLD), "Bcast(rc)");
    return out;
}

// W2(F3)：进程亲和掩码核数（Linux sched_getaffinity；macOS 无该接口 ⇒ 0 = 未知，
// 调用方回落 hardware_concurrency）。taskset/cgroup/共享宿主的核预算由此对 root 相
// 可见。获取失败同样返回 0（按"未知"处理，不猜）。内部链接：本 TU 私有薄件。
static size_t affinityThreads() {
#if defined(__APPLE__)
    return 0;
#else
    cpu_set_t set;
    CPU_ZERO(&set);
    if (sched_getaffinity(0, sizeof(set), &set) != 0) return 0;
    const int n = CPU_COUNT(&set);
    return (n > 0) ? (size_t)n : 0;
#endif
}

size_t machineThreads() {
    // MESHGEN_MPI_ROOT_THREADS 显式覆盖（原样保留）：分工点后 rank0 把分布段为避免
    // 超订阅而限制的线程数补回来（否则分布段省下的时间被"rank0 只剩 1/P 线程"吃掉，
    // 实测 1.3M 胞 np=2 净收益归零，19_mpi_design.md 附录 C.3-2）。
    const char* e = std::getenv("MESHGEN_MPI_ROOT_THREADS");
    if (e && *e) {
        const long v = std::strtol(e, nullptr, 10);
        if (v > 0) return (size_t)v;
    }
    // W2(F3) 修复：缺省 = min(机器逻辑核数, 进程亲和掩码核数)。旧缺省
    // hardware_concurrency 的前提"分工点后 rank0 独占机器"在 taskset/cgroup/共享宿主
    // 下为假：growTo(48) 挤 8 亲和核 = 6× 超订阅抖动，np4 重放段 2.9× 慢化
    // （0.68-0.75× 净损，需 ROOT_THREADS=8 显式救回 ≈1.0×），见
    // 31_dc17_mpi_hardening.md F3。亲和感知后【缺省即等效显式 ROOT8】，env 覆盖保留。
    // 收割者交互：无亲和的 np 跑里旧缺省还会把 rank0 推上 4800% 瞬时 CPU（F6 同款
    // SIGTERM 风险），新缺省在 taskset 姿势下自动封顶。
    // 纯性能项：W-不变性已实证（同产物 8/16/24/48 线程逐字节同，§2.3/附录 C.3-1）
    // ⇒ 字节红线无涉。
    const size_t hw = std::thread::hardware_concurrency();
    const size_t aff = affinityThreads();
    size_t n = hw ? hw : 1;
    if (aff > 0 && aff < n) n = aff;
    return n;
}

bool enterRootPhase() {
    if (!active()) return true;
    g_split = true;                 // 全秩同点置位：此后 consensusExit/barrier 退化为恒等
    globalPool().clearSegment();    // E2：段限域钩子必须在此前摘除（rank0 串行重放 = 全量域）
    g_segLo = g_segHi = 0;
    if (g_size == 1) return true;
    if (g_rank != 0) return false;  // 非 rank0：调用方立即返回，CLI 跳过导出，main 退出 0
    globalPool().growTo(machineThreads());   // 其余秩已无工作 ⇒ 拿回全部并行度
    return true;
}

bool anyFail(const std::string& localErr, std::string& globalErr) {
    globalErr = localErr;
    if (!active()) return !localErr.empty();
    // 报错源 = 有错秩中的最小 rank（无错秩键 = INT_MAX）⇒ 全秩同 err、同退出码，
    // 由 CLI 侧 rank0 打印一份（避免 P 份重复 stderr，§2.e 报错路径口径）。
    const int key = localErr.empty() ? INT_MAX : g_rank;
    int src = INT_MAX;
    chk(MPI_Allreduce(&key, &src, 1, MPI_INT, MPI_MIN, MPI_COMM_WORLD), "Allreduce(srcKey)");
    if (src == INT_MAX) { globalErr.clear(); return false; }
    globalErr = localErr;   // src 秩的串由下面的 bcastStringFrom 覆盖到全秩
    bcastStringFrom(src, globalErr);
    return true;
}

// ---------------- 分解 ----------------
void rangeOf(int r, long long n, long long& lo, long long& hi) {
    const long long P = g_size > 0 ? g_size : 1;
    if (n <= 0 || r < 0 || r >= P) { lo = hi = 0; return; }
    const long long base = n / P, rem = n % P;
    lo = base * r + std::min<long long>(r, rem);
    hi = lo + base + (r < rem ? 1 : 0);
}
void range(long long n, long long& lo, long long& hi) {
    if (!active()) { lo = 0; hi = n; return; }
    rangeOf(g_rank, n, lo, hi);
}

void parallelRanges(long long lo, long long hi,
                    const std::function<void(size_t, size_t)>& body) {
    if (hi <= lo) return;
    const long long off = lo;
    // E2：本原语 = 显式区间，必须不受段限域钩子影响（否则区间被二次裁剪）。
    // 临时摘钩执行后恢复（调用契约：主线程调用；钩子只在任务派发时读取）。
    const bool wasSeg = globalPool().segmentActive();
    if (wasSeg) globalPool().clearSegment();
    globalPool().parallelRanges((size_t)(hi - lo), [&](size_t a, size_t b) {
        body((size_t)(off + (long long)a), (size_t)(off + (long long)b));
    });
    if (wasSeg) globalPool().setSegment(g_segLo, g_segHi, g_segMul);
}

// ---------------- E2：段限域钩子转发 ----------------
void poolSetSegment(long long lo, long long hi, int virtMul) {
    g_segLo = lo; g_segHi = hi; g_segMul = virtMul;
    globalPool().setSegment(lo, hi, virtMul);
}
void poolSetSegmentExtras(std::vector<size_t> ex) { globalPool().setSegmentExtras(std::move(ex)); }
void poolClearSegment() { globalPool().clearSegment(); }
bool poolSegmentActive() { return globalPool().segmentActive(); }

// ---------------- 集合通信薄层 ----------------
void allgatherLLPairs(std::vector<std::pair<long long, long long>>& mine,
                      std::vector<std::pair<long long, long long>>& all) {
    all.clear();
    if (!active() || g_size == 1) {
        all = mine;
        return;
    }
    std::vector<long long> flat;
    flat.reserve(mine.size() * 2);
    for (const auto& p : mine) {
        flat.push_back(p.first);
        flat.push_back(p.second);
    }
    std::vector<long long> out;
    allgatherFlat(flat, out, MPI_LONG_LONG_INT, "Allgatherv(wavePairs)");
    if (out.size() % 2 != 0) commFatal("allgatherLLPairs: 奇数长度", -1);
    all.resize(out.size() / 2);
    for (size_t i = 0; i < all.size(); ++i) all[i] = {out[2 * i], out[2 * i + 1]};
}

void allgatherLL(std::vector<long long>& v) {
    if (!active() || g_size == 1) return;
    std::vector<long long> out;
    allgatherFlat(v, out, MPI_LONG_LONG_INT, "Allgatherv(candidates)");
    v.swap(out);
}

// W1(F1) 修复口径（三处同款，31_dc17_mpi_hardening.md F1）：sendbuf 落在 recvbuf 区间内
// 的别名 Allgatherv 违反 MPI-3.1 §5.6——in-place 语义的标准表达【只有】MPI_IN_PLACE
// （此时本秩贡献 = recvbuf+dsp[rank] 处的 recvcounts[rank] 元素，sendcount/sendtype 被
// 忽略）。MPICH 4.3.2 严格检查别名 ⇒ 全秩 Abort（宿主生产 MPI 上 np≥2 阻断）；Open MPI
// 5.0.10 容忍同一代码属历史巧合。位等价论证：调用契约 = lo/hi 来自 range()/rangeOf()
// 等分（或 U8V 的秩主序前缀和像）⇒ lo==dsp[rank] 且 hi-lo==cnt[rank]，旧行为"从
// v+lo 发送"与 in-place"从 recvbuf+dsp[rank] 读取"是同一段字节 ⇒ 结果逐位同一，纯通信
// 层修复、产物序不变。契约破坏（秩间分解错位）⇒ 诚实中止，与 allgatherU8V 既有守卫同纪律。
void allgatherI8(std::vector<int8_t>& v, long long lo, long long hi) {
    if (!active() || g_size == 1) return;
    const Seg s = segments((long long)v.size());
    if (s.dsp[(size_t)g_rank] != (int)lo || s.cnt[(size_t)g_rank] != (int)(hi - lo))
        commFatal("allgatherI8: 段界与等分不符（MPI_IN_PLACE 前提被破坏）", -1);
    chk(MPI_Allgatherv(MPI_IN_PLACE, 0, MPI_INT8_T, v.data(), s.cnt.data(),
                       s.dsp.data(), MPI_INT8_T, MPI_COMM_WORLD),
        "Allgatherv(i8)");
}

void allgatherU8(std::vector<uint8_t>& v, long long lo, long long hi) {
    if (!active() || g_size == 1) return;
    const Seg s = segments((long long)v.size());
    if (s.dsp[(size_t)g_rank] != (int)lo || s.cnt[(size_t)g_rank] != (int)(hi - lo))
        commFatal("allgatherU8: 段界与等分不符（MPI_IN_PLACE 前提被破坏）", -1);
    chk(MPI_Allgatherv(MPI_IN_PLACE, 0, MPI_UINT8_T, v.data(), s.cnt.data(),
                       s.dsp.data(), MPI_UINT8_T, MPI_COMM_WORLD),
        "Allgatherv(u8)");
}

void allgatherU8V(std::vector<uint8_t>& v, long long lo, long long hi) {
    if (!active() || g_size == 1) return;
    if (hi - lo > (long long)INT_MAX || (long long)v.size() > (long long)INT_MAX)
        commFatal("allgatherU8V: 超 int 上限", -1);
    const int m = (int)(hi - lo);
    std::vector<int> cnt((size_t)g_size, 0), dsp((size_t)g_size, 0);
    chk(MPI_Allgather(&m, 1, MPI_INT, cnt.data(), 1, MPI_INT, MPI_COMM_WORLD),
        "Allgather(u8cnt)");
    long long tot = 0;
    for (int r = 0; r < g_size; ++r) {
        dsp[(size_t)r] = (int)tot;
        tot += cnt[(size_t)r];
    }
    if (tot != (long long)v.size() || dsp[(size_t)g_rank] != lo) {
        std::fprintf(stderr, "[mpi] DBG u8v rank=%d lo=%lld hi=%lld tot=%lld vsize=%zu dsp=%d cnt0=%d\n",
                     g_rank, lo, hi, tot, v.size(), dsp[(size_t)g_rank], cnt[0]);
        std::fflush(stderr);
        commFatal("allgatherU8V: 段界与全量不符（秩间分解错位）", -1);
    }
    // W1(F1)：MPI_IN_PLACE（论证同 allgatherI8 头注；上方既有守卫 tot==v.size() 且
    // dsp[rank]==lo 恰为 in-place 前提 ⇒ 本函数的契约检查已强制之，无需新增）。
    chk(MPI_Allgatherv(MPI_IN_PLACE, 0, MPI_UINT8_T, v.data(), cnt.data(), dsp.data(),
                       MPI_UINT8_T, MPI_COMM_WORLD),
        "Allgatherv(u8v)");
}

void allgatherBytes(const std::vector<char>& mine, std::vector<char>& out) {
    out.clear();
    if (!active() || g_size == 1) { out = mine; return; }
    allgatherFlat(mine, out, MPI_CHAR, "Allgatherv(bytes)");
}

void gatherBytesToRoot(const std::vector<char>& mine, std::vector<char>& outRoot) {
    outRoot.clear();
    if (!active()) return;
    if (g_size == 1) { outRoot = mine; return; }
    // TEMP-DIAG(31b)：取证探针——实测各秩 blob 尺寸与 root 端总量（复现后即随修复移除）
    std::fprintf(stderr, "[TEMP-DIAG] rank=%d mine=%zu B = %.3f GiB (INT_MAX=%.3f GiB)\n",
                 g_rank, mine.size(), (double)mine.size() / 1073741824.0,
                 (double)INT_MAX / 1073741824.0);
    std::fflush(stderr);
    if (mine.size() > (size_t)INT_MAX) commFatal("gatherBytesToRoot", -1);
    const int m = (int)mine.size();
    std::vector<int> cnt((size_t)g_size, 0), dsp((size_t)g_size, 0);
    chk(MPI_Gather(&m, 1, MPI_INT, cnt.data(), 1, MPI_INT, 0, MPI_COMM_WORLD), "Gather(len)");
    if (g_rank == 0) {
        long long tot = 0;
        for (int r = 0; r < g_size; ++r) {
            if (tot > (long long)INT_MAX) commFatal("gatherBytesToRoot", -1);
            dsp[(size_t)r] = (int)tot;
            tot += cnt[(size_t)r];
        }
        // TEMP-DIAG(31b)：root 端全量（触发 L433 tot 守卫的实测量）
        std::fprintf(stderr, "[TEMP-DIAG] root tot=%lld B = %.3f GiB = %.3fx INT_MAX\n", tot,
                     (double)tot / 1073741824.0, (double)tot / (double)INT_MAX);
        std::fflush(stderr);
        if (tot > (long long)INT_MAX) commFatal("gatherBytesToRoot", -1);
        outRoot.assign((size_t)tot, '\0');
    }
    chk(MPI_Gatherv(m ? mine.data() : nullptr, m, MPI_CHAR,
                    g_rank == 0 && !outRoot.empty() ? outRoot.data() : nullptr,
                    cnt.data(), dsp.data(), MPI_CHAR, 0, MPI_COMM_WORLD),
        "Gatherv(bytes)");
}

void bcastBytes(std::vector<char>& buf, int root) {
    if (!active() || g_size == 1) return;
    int n = (int)std::min<size_t>(buf.size(), (size_t)INT_MAX);
    if (buf.size() > (size_t)INT_MAX) commFatal("bcastBytes", -1);
    if (g_rank != root) n = 0;
    chk(MPI_Bcast(&n, 1, MPI_INT, root, MPI_COMM_WORLD), "Bcast(bytesLen)");
    buf.resize((size_t)n);
    if (n > 0) chk(MPI_Bcast(buf.data(), n, MPI_CHAR, root, MPI_COMM_WORLD), "Bcast(bytes)");
}

bool allreduceOr(bool v) {
    if (!active() || g_size == 1) return v;
    int in = v ? 1 : 0, out = 0;
    chk(MPI_Allreduce(&in, &out, 1, MPI_INT, MPI_LOR, MPI_COMM_WORLD), "Allreduce(or)");
    return out != 0;
}

// ---------------- F2：dist 尾 run-end 状态同步（失败即全秩同步，杜绝秩失步挂死） ----
// engaged 由引擎在 dist 尾分工点（S11 前 Gatherv 处）全秩同点置位；generateCartesian
// 末尾全秩调 syncFinalStatus：rank0 广播 (ok, err)，非 rank0 阻塞接收并覆盖自身结果。
// 注意：此时 g_split 已置位（enterRootPhase 已过）——本函数是分工点后【唯一】约定的
// 集合点（非 rank0 在 MPI_Finalize 前必然到达），不受 g_split 恒等化影响。
void setDistEngaged(bool v) { g_distEngaged = v; }
bool distEngaged() { return g_distEngaged; }
void setDistTailMode(const char* label) { g_distTailMode = label; }
const char* distTailMode() { return g_distTailMode; }
void syncFinalStatus(bool& ok, std::string& err) {
    if (!active() || g_size == 1 || !g_distEngaged) return;
    int v = ok ? 1 : 0;
    chk(MPI_Bcast(&v, 1, MPI_INT, 0, MPI_COMM_WORLD), "Bcast(finalOk)");
    bcastStringFrom(0, err);
    ok = (v != 0);
    g_distEngaged = false;   // 一次性：每次 assembleMesh 分工点重新置位
}

long long scanExclusiveLL(long long v) {
    if (!active() || g_size == 1) return 0;
    std::vector<long long> all((size_t)g_size, 0);
    chk(MPI_Allgather(&v, 1, MPI_LONG_LONG_INT, all.data(), 1, MPI_LONG_LONG_INT,
                      MPI_COMM_WORLD),
        "Allgather(scan)");
    long long acc = 0;
    for (int r = 0; r < g_rank; ++r) acc += all[(size_t)r];
    return acc;
}

long long physMemMB() {
#if defined(__APPLE__)
    int64_t memsz = 0;
    size_t len = sizeof(memsz);
    if (sysctlbyname("hw.memsize", &memsz, &len, nullptr, 0) == 0 && memsz > 0)
        return (long long)(memsz / (1024 * 1024));
    return 0;
#else
    long pages = sysconf(_SC_PHYS_PAGES);
    long pageSize = sysconf(_SC_PAGESIZE);
    if (pages > 0 && pageSize > 0) return (long long)pages * pageSize / (1024 * 1024);
    return 0;
#endif
}

void allgatherLists(std::vector<std::vector<int>>& v, long long lo, long long hi) {
    if (!active() || g_size == 1) return;
    const long long n = (long long)v.size();
    // 稀疏编码（PERF）：只传本段【非空】条目 (全局叶号, 条目数, 载荷)。
    // 稠密口径要传 n 个 int32 计数（30M 叶 = 120MB/次，而 hitTris 每 S5 pass 各一次）；
    // 切割叶占比实测 ~0.2%（30M 叶 / 8.2 万切割叶）⇒ 稀疏后 ~1MB/次，降两个数量级。
    // 语义等价：调用方契约 = 各秩只填本段且段外为空（computeHitTris 先 assign(n,{})），
    // 故"全量非空条目集合"= 各秩非空条目之并，rank 序拼接后逐条写回即复原全量表。
    std::vector<long long> idxMine;
    std::vector<int> cntMine, payMine;
    for (long long i = lo; i < hi; ++i) {
        const auto& l = v[(size_t)i];
        if (l.empty()) continue;
        if (l.size() > (size_t)INT_MAX) commFatal("allgatherLists: 单叶条目超 int 上限", -1);
        idxMine.push_back(i);
        cntMine.push_back((int)l.size());
        payMine.insert(payMine.end(), l.begin(), l.end());
    }
    std::vector<long long> idxAll;
    std::vector<int> cntAll, payAll;
    allgatherFlat(idxMine, idxAll, MPI_LONG_LONG_INT, "Allgatherv(listIdx)");
    allgatherFlat(cntMine, cntAll, MPI_INT, "Allgatherv(listCounts)");
    allgatherFlat(payMine, payAll, MPI_INT, "Allgatherv(listPayload)");
    if (idxAll.size() != cntAll.size()) commFatal("allgatherLists: 索引/计数长度不符", -1);
    // 防御性清空（契约上段外本就为空；O(n) 次空 vector  clear，无分配）
    for (long long i = 0; i < n; ++i)
        if (!v[(size_t)i].empty()) v[(size_t)i].clear();
    long long off = 0;
    for (size_t k = 0; k < idxAll.size(); ++k) {
        const long long c = cntAll[k];
        const long long idx = idxAll[k];
        if (idx < 0 || idx >= n || off + c > (long long)payAll.size())
            commFatal("allgatherLists: 收到越界条目（秩间数据错位）", -1);
        v[(size_t)idx].assign(payAll.begin() + off, payAll.begin() + off + c);
        off += c;
    }
}

void allgatherProposals(std::vector<std::pair<long long, int8_t>>& mine,
                        std::vector<std::pair<long long, int8_t>>& all) {
    all.clear();
    if (!active() || g_size == 1) {
        all = mine;
        return;
    }
    // 打平成两个数组（int64 索引 + int8 值），各自 allgatherv（rank 序拼接）
    std::vector<long long> sidx;
    std::vector<int8_t> sval;
    sidx.reserve(mine.size());
    sval.reserve(mine.size());
    for (const auto& p : mine) {
        sidx.push_back(p.first);
        sval.push_back(p.second);
    }
    std::vector<long long> idxAll;
    std::vector<int8_t> valAll;
    allgatherFlat(sidx, idxAll, MPI_LONG_LONG_INT, "Allgatherv(propIdx)");
    allgatherFlat(sval, valAll, MPI_INT8_T, "Allgatherv(propVal)");
    if (idxAll.size() != valAll.size()) commFatal("allgatherProposals: 长度不符", -1);
    all.resize(idxAll.size());
    for (size_t i = 0; i < idxAll.size(); ++i) all[i] = {idxAll[i], valAll[i]};
}

long long allreduceSum(long long v) {
    if (!active() || g_size == 1) return v;
    long long out = 0;
    chk(MPI_Allreduce(&v, &out, 1, MPI_LONG_LONG_INT, MPI_SUM, MPI_COMM_WORLD), "Allreduce(sum)");
    return out;
}
long long allreduceMax(long long v) {
    if (!active() || g_size == 1) return v;
    long long out = 0;
    chk(MPI_Allreduce(&v, &out, 1, MPI_LONG_LONG_INT, MPI_MAX, MPI_COMM_WORLD), "Allreduce(max)");
    return out;
}

void bcastString(std::string& s) { bcastStringFrom(0, s); }

// ---------------- 握手 / 金丝雀 ----------------
bool handshake(const Vec3& origin, double H, int base, int maxLv, int Ls, long long nLeaves,
               const TriSurface& surf, std::string& err) {
    err.clear();
    if (!active() || g_size == 1) return true;
    // 位模式指纹：域参数 + 表面（顶点/三角形/patch 计数 + 坐标与索引 FNV-1a）
    uint64_t h = 0xcbf29ce484222325ull;
    h = mix(h, bits(origin.x));
    h = mix(h, bits(origin.y));
    h = mix(h, bits(origin.z));
    h = mix(h, bits(H));
    h = mix(h, (uint64_t)(int64_t)base);
    h = mix(h, (uint64_t)(int64_t)maxLv);
    h = mix(h, (uint64_t)(int64_t)Ls);
    h = mix(h, (uint64_t)nLeaves);
    h = mix(h, (uint64_t)surf.verts.size());
    h = mix(h, (uint64_t)surf.tris.size());
    h = mix(h, (uint64_t)surf.patchNames.size());
    for (const auto& v : surf.verts) {
        h = mix(h, bits(v.x));
        h = mix(h, bits(v.y));
        h = mix(h, bits(v.z));
    }
    h = fnv64(surf.tris.data(), surf.tris.size() * sizeof(surf.tris[0]), h);
    if (!surf.patch.empty())
        h = fnv64(surf.patch.data(), surf.patch.size() * sizeof(surf.patch[0]), h);
    // ---- dc17-robust 1.10（recon_C R6，其评为「性价比最高」）：补齐指纹覆盖面 ----
    // 原指纹的三个缺口：① surf.patchNames 只哈希了【数量】不含字符串内容；
    // ② surf.firstLayer（逐 patch 第一层高度）完全未哈希；③ 全部 CART_* 旋钮与
    // MESHGEN_THREADS 完全未哈希。⇒ 异构 env / 异构 patch 命名下【全秩静默通过握手】，
    // 随后分布面静默分叉，而握手本该是这道防线（R6「大声记账」的 MPI 侧对应物）。
    //
    // 旋钮枚举采用【遍历 environ 取全部 CART_ 前缀项 + MESHGEN_THREADS，按名排序后哈希
    // name+value】，而不是维护一份名单。两个理由：
    //   ① 自动完备——Cartesian.cpp 新增 CART_ENV_FLAG 时此处无需登记。对照
    //      distTailEnvOk()（Cartesian.cpp:6515）那份 27 键【黑名单】：recon_C R12 指出
    //      漏登记就会静默进入分布面（CART_S11_CENSUS:263 与 CART_DUAL_CENSUS:268 至今未登记）。
    //      本处用【全量枚举】从结构上消除该类遗漏。
    //   ② 按名排序 ⇒ 与 environ 的排列顺序无关 ⇒ 决定性（R6）。
    //
    // 零产物字节影响：指纹只在秩间做 MIN/MAX 比对，成功时既不打印也不落盘；
    // 同构 env（正常姿势）下各秩算出同值 ⇒ lo==hi ⇒ 行为与改动前逐点相同。
    // 不改 handshake 签名（只用已传入的 surf + getenv）⇒ 调用点 Cartesian.cpp:5441 不动。
    {
        std::vector<std::string> knobs;
        if (environ != nullptr) {
            for (char** e = environ; *e != nullptr; ++e) {
                const std::string kv(*e);
                if (kv.rfind("CART_", 0) == 0 || kv.rfind("MESHGEN_THREADS=", 0) == 0)
                    knobs.push_back(kv);
            }
        }
        std::sort(knobs.begin(), knobs.end());
        h = mix(h, (uint64_t)knobs.size());
        for (const auto& kv : knobs) h = fnv64(kv.data(), kv.size(), h);
    }
    // patchNames 的字符串内容（原来只有 size()）：长度 + 字节
    for (const auto& nm : surf.patchNames) {
        h = mix(h, (uint64_t)nm.size());
        if (!nm.empty()) h = fnv64(nm.data(), nm.size(), h);
    }
    // firstLayer 的位模式（<=0 = 不特殊加密，故直接哈希 double 位模式即可）
    h = mix(h, (uint64_t)surf.firstLayer.size());
    for (double d : surf.firstLayer) h = mix(h, bits(d));
    // 全秩比对：MIN==MAX ⇒ 位模式逐字一致（异构构建 fp 漂移 / 配置或 STL 不一致早捕）
    uint64_t lo = h, hi = h;
    chk(MPI_Allreduce(&h, &lo, 1, MPI_UNSIGNED_LONG_LONG, MPI_MIN, MPI_COMM_WORLD),
        "Allreduce(hashMin)");
    chk(MPI_Allreduce(&h, &hi, 1, MPI_UNSIGNED_LONG_LONG, MPI_MAX, MPI_COMM_WORLD),
        "Allreduce(hashMax)");
    if (lo != hi) {
        err = "MPI 握手失败：秩间启动状态位模式不一致（本秩 " + std::to_string(h) +
              " / 全秩 min " + std::to_string(lo) + " max " + std::to_string(hi) +
              "）——配置/表面文件/浮点构建不一致，拒跑（§2.h R5）";
        return false;
    }
    return true;
}

bool treeCanary(const CartesianMesh& m, const char* where, std::string& err) {
    err.clear();
    if (!active() || g_size == 1) return true;
    if (!envFlag("CART_MPI_CANARY", true)) return true;
    const long long n = m.nLeaves();
    uint64_t h = 0xcbf29ce484222325ull;
    h = mix(h, (uint64_t)n);
    h = mix(h, (uint64_t)m.nNodes());
    if (n > 0) {
        h = mix(h, m.leafKey(0));
        h = mix(h, m.leafKey(n - 1));
        // 抽样（≤65536 个键 + 对应级）：任何秩间树发散都会改变叶数/键分布，
        // 抽样在 O(1) 成本下捕获（全量扫描在 30M 叶尺度每 pass ~50ms，不可接受）
        const long long stride = std::max<long long>(1, n / 65536);
        for (long long i = 0; i < n; i += stride) {
            h = mix(h, m.leafKey(i));
            h = mix(h, (uint64_t)(int64_t)m.leafLevel(i));
        }
    }
    uint64_t lo = h, hi = h;
    chk(MPI_Allreduce(&h, &lo, 1, MPI_UNSIGNED_LONG_LONG, MPI_MIN, MPI_COMM_WORLD),
        "Allreduce(canaryMin)");
    chk(MPI_Allreduce(&h, &hi, 1, MPI_UNSIGNED_LONG_LONG, MPI_MAX, MPI_COMM_WORLD),
        "Allreduce(canaryMax)");
    if (lo != hi) {
        err = std::string("MPI 金丝雀失败（") + where +
              "）：秩间八叉树已发散（本秩摘要 " + std::to_string(h) + "）——立即拒跑，"
              "不允许发散树继续进入集合通信（§2.h R11）";
        return false;
    }
    return true;
}

void treeCanaryAbort(const CartesianMesh& m, const char* where) {
    if (!active() || g_size == 1) return;
    std::string err;
    if (treeCanary(m, where, err)) return;
    // 发散即硬失败：继续跑只会在后续集合通信错位/挂死，或产出静默错误的网格
    std::fprintf(stderr, "[mpi] FATAL %s\n", err.c_str());
    std::fflush(stderr);
    MPI_Abort(MPI_COMM_WORLD, 93);
    std::abort();
}

// ---------------- 分布算法：固体侧洪泛（computeLeafSolidity 的分布版） ----------------
bool leafSolidityDistributed(long long n, const CartesianMesh& mesh,
                             const std::vector<std::vector<int>>& hitTris,
                             const std::function<int8_t(long long, const Vec3&)>& evalSolid,
                             std::vector<int8_t>& solid, std::string& err) {
    err.clear();
    solid.assign((size_t)n, -1);
    if (!active() || g_size == 1) return false;   // 调用方回落串行实现
    long long lo = 0, hi = 0;
    range(n, lo, hi);
    auto inRange = [&](long long i) { return i >= lo && i < hi; };
    auto centre = [&](long long i, Vec3& c) {
        Vec3 mn, mx;
        mesh.leafBox(i, mn, mx);
        c = (mn + mx) * 0.5;
    };

    std::vector<long long> q;
    q.reserve((size_t)std::max<long long>(1, hi - lo) / 8 + 16);
    for (long long i = lo; i < hi; ++i)
        if (!hitTris[(size_t)i].empty()) q.push_back(i);   // 波源 = 本秩切割叶（同串行种子集）
    size_t head = 0;
    std::vector<long long> nbs;
    std::vector<std::pair<long long, int8_t>> mine, all;
    int round = 0;
    long long changed = 1;
    while (changed > 0) {
        // ① 本地洪泛至静默：只赋本秩段的叶；跨界邻居转为提案（值语义与串行同一分支）
        mine.clear();
        while (head < q.size()) {
            const long long L = q[head++];
            const bool lEmpty = hitTris[(size_t)L].empty();
            const int8_t lVal = solid[(size_t)L];
            for (int dir = 0; dir < 6; ++dir) {
                mesh.faceNeighbors(L, dir, nbs);
                for (long long nb : nbs) {
                    if (!hitTris[(size_t)nb].empty() || solid[(size_t)nb] != -1) continue;
                    int8_t v;
                    if (lEmpty && lVal != -1) {
                        v = lVal;                            // 空-空同侧传播
                    } else {
                        Vec3 cc;
                        centre(nb, cc);
                        v = evalSolid(nb, cc);               // 由切割叶首次定值
                    }
                    if (inRange(nb)) {
                        solid[(size_t)nb] = v;
                        q.push_back(nb);
                    } else {
                        mine.push_back({nb, v});
                    }
                }
            }
        }
        // ② 提案交换：allgatherv 按 rank 序拼接 ⇒ 各秩看到的提案表【逐元素同一】
        //    （同一目标叶可能收到多份提案，取值同一 = 洪泛合流，见头文件论证；
        //    应用时"首个生效、其余跳过"⇒ 应用序确定且与秩数/提案重复度无关）。
        allgatherProposals(mine, all);
        // ③ 只应用落在本秩段的提案（其余段由属主秩自己应用 ⇒ 每叶恰由其属主赋值一次，
        //    不存在两秩写同一叶的竞争）
        long long applied = 0;
        for (const auto& p : all) {
            const long long i = p.first;
            if (!inRange(i)) continue;
            if (solid[(size_t)i] != -1) continue;
            solid[(size_t)i] = p.second;
            q.push_back(i);
            ++applied;
        }
        changed = allreduceSum(applied);
        if (changed > 0 && ++round > 64) {
            err = "MPI 固体洪泛超轮数硬帽（64 轮，§2.b 口径）：秩间波前未收敛，拒跑";
            return false;
        }
    }
    // ④ 兜底（远离表面带的空叶）：逐叶独立 ⇒ 按秩段分布 + int8 场 allgather
    for (long long i = lo; i < hi; ++i) {
        if (solid[(size_t)i] != -1 || !hitTris[(size_t)i].empty()) continue;
        Vec3 cc;
        centre(i, cc);
        solid[(size_t)i] = evalSolid(i, cc);
    }
    allgatherI8(solid, lo, hi);
    return true;
}

// ---------------- 计时/诊断 ----------------
double wtick() {
    if (g_active) return MPI_Wtime();
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
void logRoot(const std::string& s) {
    if (!root()) return;
    std::fprintf(stderr, "[mpi] %s\n", s.c_str());
    std::fflush(stderr);
}

}  // namespace mpi
}  // namespace meshgen
