cmake_minimum_required(VERSION 3.20)
project(MeshGenerator VERSION 1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# SIMD：arm64 基线即 NEON；x86_64 开启 AVX2+FMA（MESHGEN_ENABLE_AVX2=OFF 可回退 SSE2，诊断用）
option(MESHGEN_ENABLE_AVX2 "x86_64 上启用 -mavx2 -mfma" ON)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64" AND MESHGEN_ENABLE_AVX2)
  if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
    add_compile_options(-mavx2 -mfma)
  endif()
endif()

option(MESHGEN_BUILD_GUI "构建可视化界面" ON)
option(MESHGEN_BUILD_TESTS "构建测试" ON)

include(FetchContent)
# DOWNLOAD_EXTRACT_TIMESTAMP 需要 CMake >= 3.24；3.22 的 ExternalProject 不识别该关键字，
# 会把它并进 URL 列表导致 "At least one entry of URL is a path" 配置失败（Linux 迁移垫片）。
if(CMAKE_VERSION VERSION_GREATER_EQUAL 3.24)
  FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
else()
  FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz)
endif()
set(JSON_BuildTests OFF CACHE INTERNAL "")
set(JSON_Install OFF CACHE INTERNAL "")
FetchContent_MakeAvailable(nlohmann_json)

add_subdirectory(core)
add_subdirectory(cli)

if(MESHGEN_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
if(MESHGEN_BUILD_GUI)
  add_subdirectory(gui)
endif()

# ---------------- 跨平台打包 (CPack) ----------------
set(CPACK_PACKAGE_NAME "MeshGen")
set(CPACK_PACKAGE_VENDOR "MeshGen")
set(CPACK_PACKAGE_VERSION_MAJOR ${PROJECT_VERSION_MAJOR})
set(CPACK_PACKAGE_VERSION_MINOR ${PROJECT_VERSION_MINOR})
set(CPACK_PACKAGE_VERSION_PATCH ${PROJECT_VERSION_PATCH})
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Multi-block structured + Cartesian octree cut-cell mesh generator")
install(TARGETS meshgen-cli RUNTIME DESTINATION bin)
if(MESHGEN_BUILD_GUI)
  install(TARGETS meshgen RUNTIME DESTINATION bin)
endif()
if(WIN32)
  set(CPACK_GENERATOR "ZIP;NSIS")
  set(CPACK_NSIS_DISPLAY_NAME "MeshGen")
elseif(APPLE)
  set(CPACK_GENERATOR "TGZ;ZIP")
else()
  set(CPACK_GENERATOR "TGZ;DEB")
  set(CPACK_DEBIAN_PACKAGE_MAINTAINER "MeshGen")
endif()
include(CPack)
