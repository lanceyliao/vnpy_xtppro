"""
vnpy_xtppro - XTP Pro Gateway for VeighNa

编译 C++ 绑定 (vnxtpxquote / vnxtpxtrader) 需要:
  - Boost.Python (boost_python3x)
  - Python 开发头文件
"""

import os
import platform
from pathlib import Path

from setuptools import Extension, setup


def get_ext_modules() -> list:
    """
    获取 C++ 扩展模块

    Linux 和 Windows 需要编译 Boost.Python 封装接口
    Mac 由于缺乏 XTP Pro 二进制库支持无法使用
    """
    # XTP Pro 绑定依赖 Boost.Python，不像 CTP 可以纯 C++ 编译
    # 需要预编译的 boost_python3x 共享库

    api_dir = Path("vnpy_xtppro/api")
    source_dir = api_dir / "source"
    include_dir = api_dir / "include"

    if platform.system() == "Linux":
        libs_dir = api_dir / "libs" / "linux_x86_64"
        libraries = ["xtpxquoteapi", "xtpxtraderapi", "boost_python39", "boost_thread", "boost_system"]
        library_dirs = [str(libs_dir)]
        include_dirs = [str(include_dir), str(source_dir)]
        extra_compile_args = ["-std=c++11", "-O3", "-fPIC", "-DUSE_64BITS"]
        extra_link_args = ["-lstdc++"]
        runtime_library_dirs = ["$ORIGIN"]

    elif platform.system() == "Windows":
        libs_dir = api_dir / "libs" / "win64"
        libraries = ["xtpxquoteapi", "xtpxtraderapi"]
        library_dirs = [str(libs_dir)]
        include_dirs = [str(include_dir), str(source_dir)]
        extra_compile_args = ["/O2", "/MT", "/DUSE_64BITS"]
        extra_link_args = []
        runtime_library_dirs = []

    else:
        return []

    vnxtpxquote = Extension(
        name="vnpy_xtppro.api.vnxtpxquote",
        sources=[str(source_dir / "vnxtpxquote.cpp")],
        include_dirs=include_dirs,
        library_dirs=library_dirs,
        libraries=libraries,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        runtime_library_dirs=runtime_library_dirs,
        language="cpp",
    )

    vnxtpxtrader = Extension(
        name="vnpy_xtppro.api.vnxtpxtrader",
        sources=[str(source_dir / "vnxtpxtrader.cpp")],
        include_dirs=include_dirs,
        library_dirs=library_dirs,
        libraries=libraries,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        runtime_library_dirs=runtime_library_dirs,
        language="cpp",
    )

    return [vnxtpxquote, vnxtpxtrader]


def get_data_files() -> list:
    """打包 SDK 运行时库"""
    data_files = []
    api_dir = Path("vnpy_xtppro/api")

    if platform.system() == "Linux":
        libs_dir = api_dir / "libs" / "linux_x86_64"
        if libs_dir.exists():
            for f in libs_dir.glob("*.so"):
                data_files.append(str(f))
    elif platform.system() == "Windows":
        libs_dir = api_dir / "libs" / "win64"
        if libs_dir.exists():
            for f in libs_dir.glob("*.dll"):
                data_files.append(str(f))

    return data_files


setup(
    ext_modules=get_ext_modules(),
    package_data={
        "vnpy_xtppro": [
            "api/libs/**/*.so",
            "api/libs/**/*.dll",
            "api/libs/**/*.lib",
            "api/include/*.h",
            "api/source/*.cpp",
            "api/source/*.h",
            "gateway/*.py",
            "etc/*.ini",
        ],
    },
)
