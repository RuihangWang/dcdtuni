add_executable(meshgen-cli main.cpp)
target_link_libraries(meshgen-cli PRIVATE meshgen_core)

if(WIN32)
  target_link_libraries(meshgen-cli PRIVATE ws2_32)
endif()
