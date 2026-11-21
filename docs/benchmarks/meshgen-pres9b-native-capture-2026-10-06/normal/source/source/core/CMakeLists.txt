add_library(meshgen_core STATIC
  src/Block.cpp
  src/Distribution.cpp
  src/Model.cpp
  src/Mesher.cpp
  src/Coupling.cpp
  src/Quality.cpp
  src/FoamWriter.cpp
  src/Refine.cpp
  src/Cartesian.cpp
  src/CartesianExport.cpp
  src/CartesianCfdrun.cpp)

target_include_directories(meshgen_core PUBLIC include)
# T-CFDRUN：CartesianCfdrun.hpp 住 src/（引擎侧摄入层，非公共 meshgen/ API）；cli/tests
# 以 #include "CartesianCfdrun.hpp" 复用 ⇒ 无条件 PUBLIC 暴露 src/（MPI 块下方再次添加
# 同目录为幂等无害）。
target_include_directories(meshgen_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_options(meshgen_core PRIVATE -Wall -Wextra)

find_package(Threads REQUIRED)
target_link_libraries(meshgen_core PUBLIC nlohmann_json::nlohmann_json Threads::Threads)

# ---------------- MPI 域分解生成（docs/cartesian_dev/19_mpi_design.md §2.g） ----------------
# 缺省 OFF：CartesianMPI.cpp 不进构建、MESHGEN_WITH_MPI 不定义 ⇒ 引擎/CLI 内全部 MPI
# 代码块（#if defined(MESHGEN_WITH_MPI)）整体消失，OFF 构建目标文件与基线逐字节同一。
# ON：新增唯一 TU src/CartesianMPI.cpp（MPI 薄层 + 分布驱动），并以 PUBLIC 定义把
# MESHGEN_WITH_MPI 传给 cli/tests（秩感知入口与冒烟测试同一开关）。
option(MESHGEN_WITH_MPI "构建 MPI 域分解生成路径（缺省 OFF；ON 需 mpirun 环境）" OFF)
if(MESHGEN_WITH_MPI)
  find_package(MPI REQUIRED COMPONENTS CXX)
  target_sources(meshgen_core PRIVATE src/CartesianMPI.cpp)
  target_compile_definitions(meshgen_core PUBLIC MESHGEN_WITH_MPI=1)
  target_link_libraries(meshgen_core PUBLIC MPI::MPI_CXX)
  # CartesianMPI.hpp 住 src/（引擎内部头，不属公共 API）；ON 构建才暴露该目录
  target_include_directories(meshgen_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/src)
  message(STATUS "MESHGEN_WITH_MPI=ON: ${MPI_CXX_COMPILER} (${MPI_CXX_LIBRARY_VERSION_STRING})")
endif()
