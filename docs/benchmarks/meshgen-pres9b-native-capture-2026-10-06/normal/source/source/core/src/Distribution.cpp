#include "meshgen/Distribution.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>

namespace meshgen {

const char* Distribution::name(DistType t) {
    switch (t) {
        case DistType::Uniform:  return "uniform";
        case DistType::Geometric: return "geometric";
        case DistType::Tanh:     return "tanh";
        case DistType::TanhSym:  return "tanhSym";
        case DistType::ExpSym:   return "expSym";
    }
    return "uniform";
}

DistType Distribution::fromName(const std::string& s, bool* ok) {
    if (ok) *ok = true;
    if (s == "uniform") return DistType::Uniform;
    if (s == "geometric" || s == "exp" || s == "exponential" || s == "ratio")
        return DistType::Geometric;
    if (s == "tanh") return DistType::Tanh;
    if (s == "tanhSym" || s == "tanh_sym" || s == "symTanh") return DistType::TanhSym;
    if (s == "expSym" || s == "exp_sym" || s == "symExp" || s == "exponentialSym")
        return DistType::ExpSym;
    if (ok) *ok = false;
    return DistType::Uniform;
}

std::string Distribution::describe() const {
    char buf[128];
    switch (type) {
        case DistType::Uniform:
            return "uniform";
        case DistType::Geometric:
            std::snprintf(buf, sizeof(buf), "geometric(r=%.4g)", p1);
            return buf;
        case DistType::Tanh:
            std::snprintf(buf, sizeof(buf), "tanh(a=%.4g)", p1);
            return buf;
        case DistType::TanhSym:
            std::snprintf(buf, sizeof(buf), "tanhSym(a=%.4g)", p1);
            return buf;
        case DistType::ExpSym:
            std::snprintf(buf, sizeof(buf), "expSym(a=%.4g)", p1);
            return buf;
    }
    return "uniform";
}

bool Distribution::isSameAs(const Distribution& o, double tol) const {
    if (type != o.type) return false;
    if (type == DistType::Uniform) return true;
    return std::abs(p1 - o.p1) <= tol && std::abs(p2 - o.p2) <= tol;
}

std::vector<double> distributionSequence(const Distribution& d, int count) {
    std::vector<double> s(std::max(count, 1));
    if (count <= 1) {
        if (count == 1) s[0] = 0.0;
        return s;
    }
    const int n = count;
    const double m = double(n - 1);
    auto uniform = [&]() {
        for (int i = 0; i < n; ++i) s[i] = double(i) / m;
    };
    switch (d.type) {
        case DistType::Uniform:
            uniform();
            break;
        case DistType::Geometric: {
            const double r = std::clamp(d.p1, 1e-6, 1e6);
            if (std::abs(r - 1.0) < 1e-12) {
                uniform();
            } else {
                // 单元长度 ∝ r^k（k=0..n-2）；s_k = Σ_{i<k} r^i / Σ_{i<m} r^i
                const double lr = std::log(r);
                const double denom = std::expm1(m * lr);
                for (int i = 0; i < n; ++i) s[i] = std::expm1(double(i) * lr) / denom;
            }
            break;
        }
        case DistType::Tanh: {
            const double a = std::max(d.p1, 0.0);
            if (a < 1e-6) {
                uniform();
            } else {
                const double ta = std::tanh(a);
                for (int i = 0; i < n; ++i) s[i] = std::tanh(a * (double(i) / m)) / ta;
            }
            break;
        }
        case DistType::TanhSym: {
            const double a = std::max(d.p1, 0.0);
            if (a < 1e-6) {
                uniform();
            } else {
                const double ta = std::tanh(a);
                for (int i = 0; i < n; ++i)
                    s[i] = 0.5 * (1.0 + std::tanh(a * (2.0 * double(i) / m - 1.0)) / ta);
            }
            break;
        }
        case DistType::ExpSym: {
            const double a = std::max(d.p1, 0.0);
            if (a < 1e-6) {
                uniform();
            } else {
                const double denom = 1.0 - std::exp(-a);
                auto half = [&](double u) { return (1.0 - std::exp(-a * u)) / denom; };
                for (int i = 0; i < n; ++i) {
                    const double x = double(i) / m;
                    s[i] = (x <= 0.5) ? 0.5 * half(2.0 * x)
                                      : 1.0 - 0.5 * half(2.0 * (1.0 - x));
                }
            }
            break;
        }
    }
    s.front() = 0.0;
    s.back() = 1.0;
    for (int i = 1; i < n; ++i)
        if (s[i] < s[i - 1]) s[i] = s[i - 1];  // 数值保险：保持单调
    return s;
}

double sequenceExpansionRatio(const std::vector<double>& s) {
    if (s.size() < 3) return 1.0;
    const double first = s[1] - s[0];
    const double last = s[s.size() - 1] - s[s.size() - 2];
    if (first <= 1e-30) return 1.0;
    return last / first;
}

}  // namespace meshgen
