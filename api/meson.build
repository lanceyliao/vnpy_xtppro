# vnpy_xtppro - XTP Pro Python Binding Build System
# 替代 CMakeLists.txt，使用 meson + ninja 编译
#
# 用法:
#   meson setup builddir
#   ninja -C builddir
#   ninja -C builddir install
#
# 依赖:
#   - meson >= 0.60
#   - ninja
#   - boost (含 python3 组件)
#   - python3 开发头文件

project(
    'vnpy_xtppro',
    'cpp',
    version: '1.1.0',
    default_options: [
        'cpp_std=c++11',
        'buildtype=release',
    ],
)

# ---- 检测平台 ----
is_linux = host_machine.system() == 'linux'
is_windows = host_machine.system() == 'windows'

# ---- 查找 Python ----
py_mod = import('python')
py = py_mod.find_installation('python3', pure: false)
py_dep = py.dependency()

# ---- 查找 Boost.Python ----
# 尝试多种命名格式: boost_python39, boost_python3, boost_python-py39
py_ver = py.version()
py_major = py_ver.split('.').get(0)
py_minor = py_ver.split('.').get(1)

boost_python_names = [
    'boost_python' + py_major + py_minor,     # boost_python39
    'boost_python' + py_major,                 # boost_python3
    'boost_python-py' + py_major + py_minor,  # boost_python-py39
]

boost_python_dep = dependency('', required: false)
foreach name : boost_python_names
    if not boost_python_dep.found()
        boost_python_dep = dependency(name, required: false)
    endif
endforeach

if not boost_python_dep.found()
    error('Boost.Python not found. Tried: ' + ' '.join(boost_python_names) +
          '\nInstall boost and compile: bjam --with-python variant=release link=shared')
endif

message('Using Boost.Python: ' + boost_python_dep.name())

# ---- Boost Thread/System (Linux) ----
if is_linux
    boost_thread_dep = dependency('boost_thread', required: true)
    boost_system_dep = dependency('boost_system', required: true)
else
    boost_thread_dep = dependency('', required: false)
    boost_system_dep = dependency('', required: false)
endif

# ---- XTP Pro SDK 头文件 ----
api_include = include_directories('include')
source_include = include_directories('source')

# ---- XTP Pro SDK 预编译库 ----
if is_linux
    sdk_lib_dir = meson.current_source_dir() / 'libs' / 'linux_x86_64'
    xtpx_quote_lib = declare_dependency(
        link_args: [sdk_lib_dir / 'libxtpxquoteapi.so'],
    )
    xtpx_trader_lib = declare_dependency(
        link_args: [sdk_lib_dir / 'libxtpxtraderapi.so'],
    )
elif is_windows
    sdk_lib_dir = meson.current_source_dir() / 'libs' / 'win64'
    xtpx_quote_lib = declare_dependency(
        link_args: [sdk_lib_dir / 'xtpxquoteapi.lib'],
    )
    xtpx_trader_lib = declare_dependency(
        link_args: [sdk_lib_dir / 'xtpxtraderapi.lib'],
    )
endif

# ---- 编译选项 ----
cpp_args = ['-DUSE_64BITS', '-DBUILD_XTP_MD', '-DBUILD_XTP_TD']
if is_linux
    cpp_args += ['-fPIC']
endif

# ---- Python site-packages 安装路径 ----
site_packages = py.get_path('purelib')

# ---- 行情绑定 (vnxtpxquote) ----
vnxtpxquote = shared_module(
    'vnxtpxquote',
    'source/vnxtpxquote.cpp',
    include_directories: [api_include, source_include],
    dependencies: [
        py_dep,
        boost_python_dep,
        boost_thread_dep,
        boost_system_dep,
        xtpx_quote_lib,
    ],
    cpp_args: cpp_args,
    install: true,
    install_dir: site_packages,
)

# ---- 交易绑定 (vnxtpxtrader) ----
vnxtpxtrader = shared_module(
    'vnxtpxtrader',
    'source/vnxtpxtrader.cpp',
    include_directories: [api_include, source_include],
    dependencies: [
        py_dep,
        boost_python_dep,
        boost_thread_dep,
        boost_system_dep,
        xtpx_trader_lib,
    ],
    cpp_args: cpp_args,
    install: true,
    install_dir: site_packages,
)

# ---- 安装 SDK 运行时库到 site-packages ----
if is_linux
    install_data(
        'libs/linux_x86_64/libxtpxquoteapi.so',
        'libs/linux_x86_64/libxtpxtraderapi.so',
        install_dir: site_packages,
    )
elif is_windows
    install_data(
        'libs/win64/xtpxquoteapi.dll',
        'libs/win64/xtpxtraderapi.dll',
        install_dir: site_packages,
    )
endif
