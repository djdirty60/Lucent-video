cmake_minimum_required(VERSION 3.20)
project(vfs.lucent C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(KODI_DEVKIT "${CMAKE_CURRENT_SOURCE_DIR}/vendor/kodi-dev-kit/include" CACHE PATH "Kodi devkit include directory")

set(ENABLE_PROGRAMS OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(MBEDTLS_FATAL_WARNINGS OFF CACHE BOOL "" FORCE)
set(GEN_FILES OFF CACHE BOOL "" FORCE)
set(USE_SHARED_MBEDTLS_LIBRARY OFF CACHE BOOL "" FORCE)
set(USE_STATIC_MBEDTLS_LIBRARY ON CACHE BOOL "" FORCE)
add_subdirectory(vendor/mbedtls EXCLUDE_FROM_ALL)

# A log line whose values do not fit its text stops the build (0.1.6 of the check shipped one).
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  add_compile_options($<$<COMPILE_LANGUAGE:CXX>:-Werror=format>)
endif()

add_library(lucent_core STATIC
  src/engine/common.cpp
  src/engine/plan.cpp
  src/engine/yenc.cpp
  src/engine/transport.cpp
  src/engine/nntp.cpp
  src/engine/stream.cpp
  src/engine/checker.cpp
  src/engine/device.cpp)
target_include_directories(lucent_core PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}/src"
  "${CMAKE_CURRENT_SOURCE_DIR}/vendor/mbedtls/include")
target_link_libraries(lucent_core PUBLIC mbedtls mbedx509 mbedcrypto)
if(WIN32)
  target_link_libraries(lucent_core PUBLIC ws2_32 crypt32)
elseif(ANDROID)
  target_link_libraries(lucent_core PUBLIC log)
endif()

# Two Kodi add-ons from one engine: vfs.lucent (plays) and vfs.lucentcheck (checks a release before you pick it).
function(lucent_addon target id source folder)
  add_library(${target} SHARED ${source})
  target_include_directories(${target} PRIVATE "${KODI_DEVKIT}")
  set(defines BUILD_KODI_ADDON ADDON_GLOBAL_VERSION_MAIN_USED ADDON_GLOBAL_VERSION_GENERAL_USED
      ADDON_GLOBAL_VERSION_FILESYSTEM_USED ADDON_INSTANCE_VERSION_VFS_USED)
  if(ANDROID)
    target_compile_definitions(${target} PRIVATE ${defines} TARGET_ANDROID TARGET_POSIX)
    set(prefix "lib")
    set(suffix ".so")
  elseif(UNIX)
    target_compile_definitions(${target} PRIVATE ${defines} TARGET_POSIX TARGET_LINUX)
    set(prefix "lib")
    set(suffix ".so")
  else()
    target_compile_definitions(${target} PRIVATE ${defines} TARGET_WINDOWS)
    set(prefix "")
    set(suffix ".dll")
  endif()
  set_target_properties(${target} PROPERTIES
    PREFIX "${prefix}"
    OUTPUT_NAME "${id}"
    SUFFIX "${suffix}"
    CXX_VISIBILITY_PRESET hidden
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/${id}"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/${id}")
  target_link_libraries(${target} PRIVATE lucent_core)
  if(WIN32)
    target_link_options(${target} PRIVATE -static -static-libgcc -static-libstdc++ -s)
  elseif(ANDROID)
    target_link_options(${target} PRIVATE -Wl,-s)
  else()
    # Linux (CoreELEC, LibreELEC, desktop): the C++ runtime inside the add-on, so it loads next to any Kodi build
    target_link_options(${target} PRIVATE -static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL -Wl,-s -Wl,--no-undefined)
  endif()
  add_custom_command(TARGET ${target} POST_BUILD
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${folder}/addon.xml" "$<TARGET_FILE_DIR:${target}>/addon.xml"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${folder}/icon.png" "$<TARGET_FILE_DIR:${target}>/icon.png"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE.txt" "$<TARGET_FILE_DIR:${target}>/LICENSE.txt"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
            "${CMAKE_CURRENT_SOURCE_DIR}/THIRD-PARTY-NOTICES.txt" "$<TARGET_FILE_DIR:${target}>/THIRD-PARTY-NOTICES.txt"
    COMMAND "${CMAKE_COMMAND}" -E copy_directory
            "${CMAKE_CURRENT_SOURCE_DIR}/licenses" "$<TARGET_FILE_DIR:${target}>/licenses")
endfunction()

lucent_addon(vfs_lucent vfs.lucent src/addon.cpp "${CMAKE_CURRENT_SOURCE_DIR}")
lucent_addon(vfs_lucentcheck vfs.lucentcheck src/check_addon.cpp "${CMAKE_CURRENT_SOURCE_DIR}/check")

add_executable(stream_test tools/stream_test.cpp)
target_link_libraries(stream_test PRIVATE lucent_core)
if(WIN32)
  target_link_options(stream_test PRIVATE -static -static-libgcc -static-libstdc++)
endif()
set_target_properties(stream_test PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(seek_test tools/seek_test.cpp)
target_link_libraries(seek_test PRIVATE lucent_core)
set_target_properties(seek_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(side_test tools/side_test.cpp)
target_link_libraries(side_test PRIVATE lucent_core)
set_target_properties(side_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(mile_test tools/mile_test.cpp)
target_link_libraries(mile_test PRIVATE lucent_core)
set_target_properties(mile_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(cues_test tools/cues_test.cpp)
target_link_libraries(cues_test PRIVATE lucent_core)
set_target_properties(cues_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(held_test tools/held_test.cpp)
target_link_libraries(held_test PRIVATE lucent_core)
set_target_properties(held_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(device_test tools/device_test.cpp)
target_link_libraries(device_test PRIVATE lucent_core)
set_target_properties(device_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(check_test tools/check_test.cpp)
target_link_libraries(check_test PRIVATE lucent_core)
set_target_properties(check_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")

add_executable(head_tail_test tools/head_tail_test.cpp)
target_link_libraries(head_tail_test PRIVATE lucent_core)
set_target_properties(head_tail_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/dist/tools")
