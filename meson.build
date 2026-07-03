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
# XTP Pro 绑定依赖 Boost.Python（不同于 CTP 的 pybind11）
py_ver = py.version()
py_major = py_ver.split('.').get(0)
py_minor = py_ver.split('.').get(1)

# 尝试多种方式查找 Boost.Python
boost_python_dep = dependency('', required: false)

# 1. 尝试 pkg-config / cmake / meson dependency
boost_python_names = [
  'boost_python' + py_major + py_minor,     # boost_python310
  'boost_python' + py_major,                 # boost_python3
  'boost_python-py' + py_major + py_minor,  # boost_python-py310
  'Boost::python' + py_major + py_minor,    # cmake target
]

foreach name : boost_python_names
  if not boost_python_dep.found()
    boost_python_dep = dependency(name, required: false)
  endif
endforeach

# 2. 尝试编译器直接查找
if not boost_python_dep.found()
  boost_python_lib_names = [
    'boost_python' + py_major + py_minor,
    'boost_python' + py_major,
    'boost_python3',
  ]
  foreach name : boost_python_lib_names
    if not boost_python_dep.found()
      boost_python_dep = cpp.find_library(name, required: false)
    endif
  endforeach
endif

# 3. Windows: 检查 vcpkg 安装路径
if host_machine.system() == 'windows' and not boost_python_dep.found()
  vcpkg_root = run_command('cmd', '/c', 'echo %VCPKG_ROOT%', check: false).stdout().strip()
  if vcpkg_root != '' and vcpkg_root != '%VCPKG_ROOT%'
    message('Trying vcpkg Boost at: ' + vcpkg_root)
    vcpkg_include = vcpkg_root / 'installed/x64-windows/include'
    vcpkg_lib = vcpkg_root / 'installed/x64-windows/lib'
    if fs.is_dir(vcpkg_include)
      include_dirs = include_dirs, include_directories(vcpkg_include)
      foreach name : boost_python_lib_names
        if not boost_python_dep.found()
          boost_python_dep = cpp.find_library(name, dirs: [vcpkg_lib], required: false)
        endif
      endforeach
    endif
  endif

  # Also check BOOST_ROOT
  boost_root = run_command('cmd', '/c', 'echo %BOOST_ROOT%', check: false).stdout().strip()
  if boost_root != '' and boost_root != '%BOOST_ROOT%' and not boost_python_dep.found()
    message('Trying BOOST_ROOT at: ' + boost_root)
    br_include = boost_root / 'include'
    br_lib = boost_root / 'lib'
    if fs.is_dir(br_include)
      include_dirs = include_dirs, include_directories(br_include)
      foreach name : boost_python_lib_names
        if not boost_python_dep.found()
          boost_python_dep = cpp.find_library(name, dirs: [br_lib], required: false)
        endif
      endforeach
    endif
  endif
endif

# 4. Linux: 检查 /usr/local
if host_machine.system() != 'windows' and not boost_python_dep.found()
  foreach name : boost_python_lib_names
    if not boost_python_dep.found()
      boost_python_dep = cpp.find_library(name, dirs: ['/usr/local/lib'], required: false)
    endif
  endforeach
endif

if not boost_python_dep.found()
  error('Boost.Python not found! XTP Pro bindings require Boost.Python. ' +
        'Install: b2 --with-python variant=release link=shared install')
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
  link_args: extra_link_args + (host_machine.system() != 'windows' ? [
    '-L' + lib_dir,
    '-lxtpxquoteapi',
    '-Wl,-rpath,$ORIGIN',
  ] : []),
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
  link_args: extra_link_args + (host_machine.system() != 'windows' ? [
    '-L' + lib_dir,
    '-lxtpxtraderapi',
    '-Wl,-rpath,$ORIGIN',
  ] : []),
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
