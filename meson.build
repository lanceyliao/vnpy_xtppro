project(
  'vnpy_xtppro',
  'cpp',
  version: '1.1.0',
  license: 'MIT',
  meson_version: '>=1.7.0',
  default_options: [
    'buildtype=release',
    'cpp_std=c++11',
    'warning_level=1',
  ],
)

# 导入 Python 模块
py = import('python').find_installation(pure: false)
py_dep = py.dependency()

# 获取编译器信息
cpp = meson.get_compiler('cpp')

# 输出构建目标系统信息
message('构建目标系统: ' + host_machine.system())

# 初始化变量
xtpx_quote_lib = []
xtpx_trader_lib = []
extra_cpp_args = []
extra_link_args = []
lib_dir = ''
install_rpath_value = ''

# ---- Windows 编译选项 ----
if host_machine.system() == 'windows'
  add_project_arguments('/MT', language: 'cpp')

  lib_dir = meson.current_source_dir() / 'vnpy_xtppro/api/libs/win64'
  api_dir = meson.current_source_dir() / 'vnpy_xtppro/api'

  include_dirs = include_directories(
    'vnpy_xtppro/api/include',
    'vnpy_xtppro/api/source',
  )

  xtpx_quote_lib = cpp.find_library('xtpxquoteapi',
                                     dirs: [lib_dir, api_dir],
                                     required: true)
  xtpx_trader_lib = cpp.find_library('xtpxtraderapi',
                                     dirs: [lib_dir, api_dir],
                                     required: true)

# ---- Linux 编译选项 ----
else
  extra_cpp_args = [
    '-std=c++11',
    '-O3',
    '-fPIC',
    '-Wno-sign-compare',
  ]

  extra_link_args = [
    '-lstdc++',
  ]

  lib_dir = meson.current_source_dir() / 'vnpy_xtppro/api/libs/linux_x86_64'

  include_dirs = include_directories(
    'vnpy_xtppro/api/include',
    'vnpy_xtppro/api/source',
  )

  xtpx_quote_lib = cpp.find_library('xtpxquoteapi',
                                     dirs: [lib_dir],
                                     required: true)
  xtpx_trader_lib = cpp.find_library('xtpxtraderapi',
                                     dirs: [lib_dir],
                                     required: true)

  install_rpath_value = '$ORIGIN'
endif

# ---- 查找 Boost.Python ----
# Boost.Python 是 XTP Pro 绑定所必需的（不同于 CTP 的 pybind11）
py_ver = py.version()
py_major = py_ver.split('.').get(0)
py_minor = py_ver.split('.').get(1)

boost_python_names = [
  'boost_python' + py_major + py_minor,     # boost_python310
  'boost_python' + py_major,                 # boost_python3
  'boost_python-py' + py_major + py_minor,  # boost_python-py310
]

boost_python_dep = dependency('', required: false)
foreach name : boost_python_names
  if not boost_python_dep.found()
    boost_python_dep = dependency(name, required: false)
  endif
endforeach

if not boost_python_dep.found()
  # 尝试用编译器查找
  boost_python_dep = cpp.find_library(
    'boost_python' + py_major + py_minor,
    required: false,
  )
endif

if not boost_python_dep.found()
  warning('Boost.Python not found via dependency or find_library. ' +
          'XTP Pro bindings require Boost.Python. ' +
          'Compile: bjam --with-python variant=release link=shared')
endif

message('Using Boost.Python: ' + boost_python_dep.name())

# Linux 额外 Boost 依赖
if host_machine.system() != 'windows'
  boost_thread_dep = dependency('boost_thread', required: false)
  boost_system_dep = dependency('boost_system', required: false)
else
  boost_thread_dep = dependency('', required: false)
  boost_system_dep = dependency('', required: false)
endif

# ---- 编译选项 ----
extra_cpp_args += ['-DUSE_64BITS', '-DBUILD_XTP_MD', '-DBUILD_XTP_TD']

# ---- 行情绑定 (vnxtpxquote) ----
md_module = py.extension_module(
  'vnxtpxquote',
  sources: ['vnpy_xtppro/api/source/vnxtpxquote.cpp'],
  include_directories: include_dirs,
  dependencies: [py_dep, boost_python_dep, boost_thread_dep, boost_system_dep],
  cpp_args: extra_cpp_args,
  link_args: extra_link_args + [
    '-L' + lib_dir,
    '-lxtpxquoteapi',
    '-Wl,-rpath,$ORIGIN',
  ],
  install: true,
  install_rpath: install_rpath_value,
  subdir: 'vnpy_xtppro/api',
)

# ---- 交易绑定 (vnxtpxtrader) ----
td_module = py.extension_module(
  'vnxtpxtrader',
  sources: ['vnpy_xtppro/api/source/vnxtpxtrader.cpp'],
  include_directories: include_dirs,
  dependencies: [py_dep, boost_python_dep, boost_thread_dep, boost_system_dep],
  cpp_args: extra_cpp_args,
  link_args: extra_link_args + [
    '-L' + lib_dir,
    '-lxtpxtraderapi',
    '-Wl,-rpath,$ORIGIN',
  ],
  install: true,
  install_rpath: install_rpath_value,
  subdir: 'vnpy_xtppro/api',
)

# ---- 安装 Python 源代码 ----
python_files = [
  ['vnpy_xtppro/__init__.py', 'vnpy_xtppro'],
  ['vnpy_xtppro/api/__init__.py', 'vnpy_xtppro/api'],
  ['vnpy_xtppro/api/xtp_pro_md_api.py', 'vnpy_xtppro/api'],
  ['vnpy_xtppro/gateway/__init__.py', 'vnpy_xtppro/gateway'],
  ['vnpy_xtppro/gateway/xtp_pro_gateway.py', 'vnpy_xtppro/gateway'],
  ['vnpy_xtppro/gateway/xtp_pro_md.py', 'vnpy_xtppro/gateway'],
  ['vnpy_xtppro/gateway/bar_generator.py', 'vnpy_xtppro/gateway'],
]

foreach file : python_files
  py.install_sources(
    [file[0]],
    pure: false,
    subdir: file[1],
  )
endforeach

# ---- 安装 SDK 运行时库 ----
if host_machine.system() == 'windows'
  api_libs = [
    'vnpy_xtppro/api/libs/win64/xtpxquoteapi.dll',
    'vnpy_xtppro/api/libs/win64/xtpxtraderapi.dll',
  ]

  install_data(
    api_libs,
    install_dir: py.get_install_dir() / 'vnpy_xtppro/api',
  )
else  # Linux
  api_libs = [
    'vnpy_xtppro/api/libs/linux_x86_64/libxtpxquoteapi.so',
    'vnpy_xtppro/api/libs/linux_x86_64/libxtpxtraderapi.so',
  ]

  install_data(
    api_libs,
    install_dir: py.get_install_dir() / 'vnpy_xtppro/api',
  )
endif
