#pragma once
// 轻量线程池：按块并行的无锁热路径（任务内部各自写本地缓冲，无共享写竞争）。
#include <condition_variable>
#if defined(MESHGEN_WITH_MPI)
#include <cstdlib>  // MESHGEN_THREADS 解析（仅 MPI 构建；见 globalPool）
#endif
#include <functional>
#include <future>
#include <mutex>
#include <queue>
#include <thread>
#include <vector>

namespace meshgen {

class ThreadPool {
public:
    explicit ThreadPool(size_t n = 0) {
        if (n == 0) n = std::max(2u, std::thread::hardware_concurrency());
        for (size_t i = 0; i < n; ++i) workers.emplace_back([this] { loop(); });
    }
    ~ThreadPool() { stop(); }
    ThreadPool(const ThreadPool&) = delete;
    ThreadPool& operator=(const ThreadPool&) = delete;

    template <class F>
    auto submit(F&& f) -> std::future<decltype(f())> {
        using R = decltype(f());
        auto task = std::make_shared<std::packaged_task<R()>>(std::forward<F>(f));
        auto fut = task->get_future();
        {
            std::lock_guard<std::mutex> lk(mu);
            tasks.emplace([task] { (*task)(); });
        }
        cv.notify_one();
        return fut;
    }

    // 将 [0, n) 切成连续区间并行执行（每线程一个区间，缓存友好）
    void parallelRanges(size_t n, const std::function<void(size_t, size_t)>& body) {
#if defined(MESHGEN_WITH_MPI)
        // ---- E2（M1b 装配尾分布化）：段限域模式 ----
        // segOn_ 时本调用只执行本秩段 [segLo_,segHi_) ∩ [0,n)，且任务边界对齐
        // 调用方的全局 chunk 网格（chunk = ceil(n/workers)，与串行分段口径同一）：
        // 每个任务的 lo/chunk 桶下标唯一 ⇒ body 内 `segs[lo/chunk]` 类分段缓冲的
        // 写入互不冲突、桶序拼接仍 = 全局升序（body 逐元素独立 + 只写本段元素时，
        // 产物与串行全量执行在【本段元素上】逐点一致）。lo/hi 均为全局下标。
        // segExtra_：附加单点任务 body(ci, ci+1)——只允许用于"按 ci 索引写、无
        // chunk 分段缓冲"的 body（如 S10 fails[ci]）；chunk 缓冲类 body 禁用
        // （ci/chunk 可能与主段任务撞桶）。未设段（segOn_=false）时行为与原实现
        // 逐字一致（OFF 构建本块整体不存在 ⇒ .o 逐字节同一）。
        if (segOn_) {
            // 虚拟全局网格 = 实际工作者 × segVirtMul（= 秩数 P）：调用方以
            // size()（段模式返回同一虚拟值）计算 chunk = ceil(n/(W·P)) 与分段缓冲
            // ⇒ 本秩段（≈ n/P）恰好覆盖 ≈ W 个虚拟 chunk ⇒ W 个任务满线程利用，
            // 且 lo/chunk 桶下标逐任务唯一。缓冲数组尺寸 = W·P+1（各秩只写自己
            // 的 ≈W 个桶）——以少量空桶内存换取与串行【同一套】分段缓冲协议。
            const size_t nWorkers = workers.size();
            const size_t virt = nWorkers * (segVirtMul_ > 1 ? (size_t)segVirtMul_ : 1);
            const size_t chunk = std::max<size_t>(1, (n + virt - 1) / virt);
            const size_t glo = (size_t)(segLo_ > 0 ? segLo_ : 0);
            const size_t ghi = (size_t)(segHi_ < (long long)n ? segHi_ : (long long)n);
            std::vector<std::future<void>> futs;
            for (size_t a = glo / chunk * chunk; a < ghi && a < n; a += chunk) {
                const size_t b = std::min(n, std::min(ghi, a + chunk));
                const size_t lo = std::max(a, glo);
                if (b > lo) futs.push_back(submit([=] { body(lo, b); }));
            }
            for (size_t ci : segExtra_)
                if (ci < (size_t)-1) futs.push_back(submit([=] { body(ci, ci + 1); }));
            for (auto& f : futs) f.get();
            return;
        }
#endif
        if (n == 0) return;
        const size_t nWorkers = workers.size();
        const size_t chunk = std::max<size_t>(1, (n + nWorkers - 1) / nWorkers);
        std::vector<std::future<void>> futs;
        for (size_t w = 0; w < nWorkers; ++w) {
            const size_t lo = w * chunk;
            if (lo >= n) break;
            const size_t hi = std::min(n, lo + chunk);
            futs.push_back(submit([=] { body(lo, hi); }));
        }
        for (auto& f : futs) f.get();
    }

#if defined(MESHGEN_WITH_MPI)
    // E2：段限域模式下返回虚拟并行度（W×P）——调用方以此计算 chunk 网格，与钩子
    // 内部网格一致（见 parallelRanges 段分支注释）。非段模式 = 原语义逐字不变。
    size_t size() const {
        return segOn_ ? workers.size() * (segVirtMul_ > 1 ? (size_t)segVirtMul_ : 1)
                      : workers.size();
    }
#else
    size_t size() const { return workers.size(); }
#endif

#if defined(MESHGEN_WITH_MPI)
    // ---- E2（M1b 装配尾分布化）：段限域状态（MPI 构建专属；调用契约：池空闲、
    // 仅主线程设置；作用域 = 装配尾的分布阶段，出错路径由引擎侧 RAII 保证清除）----
    void setSegment(long long lo, long long hi, int virtMul = 1) {
        segLo_ = lo; segHi_ = hi; segOn_ = (hi > lo); segExtra_.clear();
        segVirtMul_ = virtMul > 1 ? virtMul : 1;
    }
    void setSegmentExtras(std::vector<size_t> ex) { segExtra_ = std::move(ex); }
    void clearSegment() { segOn_ = false; segLo_ = segHi_ = 0; segExtra_.clear(); }
    bool segmentActive() const { return segOn_; }

    // ---- MPI 构建专属（OFF 构建不含此成员 ⇒ 目标文件逐字节同一）----
    // 只增不减地把工作者扩到 n。调用点契约：池空闲（无在飞任务）且仅主线程调用。
    // 用途（19_mpi_design.md §2.f 模式 G）：分布段每秩按 MESHGEN_THREADS=核数/秩 限制
    // 线程防超订阅；进入 rank0 串行重放段时其余秩已让出 CPU ⇒ rank0 扩回机器全核，
    // 把分布段省下的时间不被"rank0 只剩 1/P 线程"吃掉。
    // 产物不受影响：引擎的两处分段口径（S7 uf-flood 边桶序、相位 2 局部点表序）
    // 均为"分段序拼接 = 全局升序"，与工作者数无关（实测 7/14 线程产物逐字节同）。
    void growTo(size_t n) {
        if (n <= workers.size()) return;
        for (size_t i = workers.size(); i < n; ++i)
            workers.emplace_back([this] { loop(); });
    }
#endif

    void stop() {
        {
            std::lock_guard<std::mutex> lk(mu);
            if (stopped) return;
            stopped = true;
        }
        cv.notify_all();
        for (auto& t : workers)
            if (t.joinable()) t.join();
        workers.clear();
    }

private:
    void loop() {
        for (;;) {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lk(mu);
                cv.wait(lk, [this] { return stopped || !tasks.empty(); });
                if (stopped && tasks.empty()) return;
                task = std::move(tasks.front());
                tasks.pop();
            }
            task();
        }
    }
    std::vector<std::thread> workers;
    std::queue<std::function<void()>> tasks;
    std::mutex mu;
    std::condition_variable cv;
    bool stopped = false;
#if defined(MESHGEN_WITH_MPI)
    // E2：段限域状态（见 parallelRanges 内注释；OFF 构建不存在 ⇒ .o 同一）
    bool segOn_ = false;
    long long segLo_ = 0, segHi_ = 0;
    int segVirtMul_ = 1;
    std::vector<size_t> segExtra_;
#endif
};

inline ThreadPool& globalPool() {
#if defined(MESHGEN_WITH_MPI)
    // ---- MPI 构建专属（MESHGEN_WITH_MPI=ON 才编译进；OFF 构建本块整体不存在 ⇒
    // 目标文件与 OFF 基线逐字节同一，19_mpi_design.md §2.g 的"零影响"口径）----
    // MESHGEN_THREADS=N：限定本进程工作线程数。mpirun 多秩同节点时防超订阅
    // （N = 物理核数 / 秩数）；未设置或非法值 ⇒ n=0 ⇒ 原 hardware_concurrency 行为。
    static ThreadPool pool([] {
        const char* e = std::getenv("MESHGEN_THREADS");
        if (!e || !*e) return (size_t)0;
        const long v = std::strtol(e, nullptr, 10);
        return (v > 0) ? (size_t)v : (size_t)0;
    }());
    return pool;
#else
    static ThreadPool pool;
    return pool;
#endif
}

}  // namespace meshgen
