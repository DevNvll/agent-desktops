cmake_minimum_required(VERSION 3.27)

project(hyprland-agent-desktops VERSION 0.1.0 LANGUAGES CXX)

find_package(PkgConfig REQUIRED)
pkg_check_modules(HYPRLAND REQUIRED IMPORTED_TARGET hyprland)
pkg_check_modules(LUA REQUIRED IMPORTED_TARGET lua)

add_library(agent-desktops SHARED src/main.cpp)
target_compile_features(agent-desktops PRIVATE cxx_std_23)
target_compile_options(agent-desktops PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(agent-desktops PRIVATE PkgConfig::HYPRLAND PkgConfig::LUA)

set_target_properties(agent-desktops PROPERTIES
  PREFIX ""
  OUTPUT_NAME "agent-desktops"
)
