#pragma once
#include <cmath>

namespace meshgen {

struct Vec3 {
    double x = 0, y = 0, z = 0;
    Vec3() = default;
    Vec3(double x_, double y_, double z_) : x(x_), y(y_), z(z_) {}

    Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(double s) const { return {x * s, y * s, z * s}; }
    Vec3 operator/(double s) const { return {x / s, y / s, z / s}; }
    Vec3 operator-() const { return {-x, -y, -z}; }
    Vec3& operator+=(const Vec3& o) { x += o.x; y += o.y; z += o.z; return *this; }
    Vec3& operator-=(const Vec3& o) { x -= o.x; y -= o.y; z -= o.z; return *this; }
    Vec3& operator*=(double s) { x *= s; y *= s; z *= s; return *this; }

    double dot(const Vec3& o) const { return x * o.x + y * o.y + z * o.z; }
    Vec3 cross(const Vec3& o) const {
        return {y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x};
    }
    double norm2() const { return dot(*this); }
    double norm() const { return std::sqrt(norm2()); }
    Vec3 normalized() const {
        double n = norm();
        return n > 1e-300 ? (*this) / n : Vec3{};
    }
    bool approxEq(const Vec3& o, double tol = 1e-12) const {
        return std::abs(x - o.x) <= tol && std::abs(y - o.y) <= tol && std::abs(z - o.z) <= tol;
    }
};

inline Vec3 operator*(double s, const Vec3& v) { return v * s; }
inline Vec3 lerp(const Vec3& a, const Vec3& b, double t) { return a + (b - a) * t; }

}  // namespace meshgen
