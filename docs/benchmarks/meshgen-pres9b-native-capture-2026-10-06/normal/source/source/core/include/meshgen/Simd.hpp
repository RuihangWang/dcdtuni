#pragma once
// 轻量可移植 SIMD 双精度向量封装。
//   arm64  -> NEON  float64x2_t (2 通道)
//   x86_64 -> AVX   __m256d     (4 通道) / SSE2 __m128d (2 通道)
//   其他   -> 标量回退
// 设计目标：核心 TFI 行内核显式向量化，同时保持单份源码。

#if defined(__aarch64__) || defined(_M_ARM64)
#include <arm_neon.h>
#define MESHGEN_SIMD_NEON 1
#elif defined(__x86_64__) || defined(_M_X64) || defined(__i386__)
#include <immintrin.h>
#if defined(__AVX__)
#define MESHGEN_SIMD_AVX 1
#else
#define MESHGEN_SIMD_SSE2 1
#endif
#endif

namespace meshgen {
namespace simd {

inline const char* backendName() {
#if defined(MESHGEN_SIMD_NEON)
    return "NEON(f64x2)";
#elif defined(MESHGEN_SIMD_AVX)
    return "AVX(f64x4)";
#elif defined(MESHGEN_SIMD_SSE2)
    return "SSE2(f64x2)";
#else
    return "scalar";
#endif
}

#if defined(MESHGEN_SIMD_NEON)

struct alignas(16) dvec {
    float64x2_t v;
    static constexpr int width = 2;
    static dvec splat(double x) { return dvec{vdupq_n_f64(x)}; }
    static dvec load(const double* p) { return dvec{vld1q_f64(p)}; }
    void store(double* p) const { vst1q_f64(p, v); }
    dvec operator+(dvec o) const { return dvec{vaddq_f64(v, o.v)}; }
    dvec operator-(dvec o) const { return dvec{vsubq_f64(v, o.v)}; }
    dvec operator*(dvec o) const { return dvec{vmulq_f64(v, o.v)}; }
    static dvec fmadd(dvec a, dvec b, dvec c) { return dvec{vfmaq_f64(c.v, a.v, b.v)}; }
};

#elif defined(MESHGEN_SIMD_AVX)

struct alignas(32) dvec {
    __m256d v;
    static constexpr int width = 4;
    static dvec splat(double x) { return dvec{_mm256_set1_pd(x)}; }
    static dvec load(const double* p) { return dvec{_mm256_loadu_pd(p)}; }
    void store(double* p) const { _mm256_storeu_pd(p, v); }
    dvec operator+(dvec o) const { return dvec{_mm256_add_pd(v, o.v)}; }
    dvec operator-(dvec o) const { return dvec{_mm256_sub_pd(v, o.v)}; }
    dvec operator*(dvec o) const { return dvec{_mm256_mul_pd(v, o.v)}; }
    static dvec fmadd(dvec a, dvec b, dvec c) {
#if defined(__FMA__)
        return dvec{_mm256_fmadd_pd(a.v, b.v, c.v)};
#else
        return a * b + c;
#endif
    }
};

#elif defined(MESHGEN_SIMD_SSE2)

struct alignas(16) dvec {
    __m128d v;
    static constexpr int width = 2;
    static dvec splat(double x) { return dvec{_mm_set1_pd(x)}; }
    static dvec load(const double* p) { return dvec{_mm_loadu_pd(p)}; }
    void store(double* p) const { _mm_storeu_pd(p, v); }
    dvec operator+(dvec o) const { return dvec{_mm_add_pd(v, o.v)}; }
    dvec operator-(dvec o) const { return dvec{_mm_sub_pd(v, o.v)}; }
    dvec operator*(dvec o) const { return dvec{_mm_mul_pd(v, o.v)}; }
    static dvec fmadd(dvec a, dvec b, dvec c) { return a * b + c; }
};

#else

struct dvec {
    double v;
    static constexpr int width = 1;
    static dvec splat(double x) { return dvec{x}; }
    static dvec load(const double* p) { return dvec{*p}; }
    void store(double* p) const { *p = v; }
    dvec operator+(dvec o) const { return dvec{v + o.v}; }
    dvec operator-(dvec o) const { return dvec{v - o.v}; }
    dvec operator*(dvec o) const { return dvec{v * o.v}; }
    static dvec fmadd(dvec a, dvec b, dvec c) { return a * b + c; }
};

#endif

}  // namespace simd
}  // namespace meshgen
