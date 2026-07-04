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

# 导入模块
fs = import('fs')
py = import('python').find_installation(pure: false)
py_dep = py.dependency()
cpp = meson.get_compiler('cpp')

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

  # Boost thread/system/chrono for static link on Windows
  boost_thread_dep = dependency('', required: false)
  boost_system_dep = dependency('', required: false)
  boost_chrono_dep = dependency('', required: false)

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
boost_static_link_args = []  # Linux: 强制 static link boost_thread/system/chrono
py_ver = py.version()
py_major = py_ver.split('.').get(0)
py_minor = py_ver.split('.').get(1)

# 尝试多种库名格式（不同平台/包管理器命名不同）
boost_python_lib_names = [
  'boost_python' + py_major + py_minor,     # boost_python310 (Linux b2)
  'boost_python' + py_major + 'py' + py_minor,  # boost_python3py310 (conda-forge)
  'boost_python' + py_major,                 # boost_python3
  'boost_python-py' + py_major + py_minor,  # boost_python-py310
  'boost_python3',
]

# Windows static link: b2 generates names like boost_python311-vc143-mt-s-x64-1_84
# CMake build (BUILD_SHARED_LIBS=OFF) may generate simpler names
if host_machine.system() == 'windows'
  boost_python_lib_names += [
    'boost_python' + py_major + py_minor,                               # CMake system layout
    'boost_python' + py_major + py_minor + '-vc143-mt-s-x64-1_84',      # b2 static x64
    'boost_python' + py_major + py_minor + '-vc143-mt-s64-1_84',        # b2 static x64 alt
    'boost_python' + py_major + py_minor + '-vc143-mt-s-x64-1_86',      # 1.86
    'boost_python' + py_major + py_minor + '-vc143-mt-s64-1_86',        # 1.86 alt
  ]
endif

# 1. 尝试 pkg-config / meson dependency
boost_python_dep = dependency('', required: false)
boost_python_names = [
  'boost_python' + py_major + py_minor,
  'boost_python' + py_major,
  'boost_python-py' + py_major + py_minor,
]
foreach name : boost_python_names
  if not boost_python_dep.found()
    boost_python_dep = dependency(name, required: false)
  endif
endforeach

# 2. 尝试编译器直接查找
if not boost_python_dep.found()
  foreach name : boost_python_lib_names
    if not boost_python_dep.found()
      boost_python_dep = cpp.find_library(name, required: false)
    endif
  endforeach
endif

# 3. Linux: 检查 /usr/local
if host_machine.system() != 'windows' and not boost_python_dep.found()
  foreach name : boost_python_lib_names
    if not boost_python_dep.found()
      boost_python_dep = cpp.find_library(name, dirs: ['/usr/local/lib'], required: false)
    endif
  endforeach
  # 额外 Boost 依赖（Linux static link: 直接用 find_library 找 b2 编译的 .a）
  # 不用 dependency('boost_thread') 因为它会找到 apt 的 shared .so，不会打包进 wheel
  boost_thread_dep = cpp.find_library('boost_thread', dirs: ['/usr/local/lib'], required: false)
  boost_system_dep = cpp.find_library('boost_system', dirs: ['/usr/local/lib'], required: false)
  boost_chrono_dep = cpp.find_library('boost_chrono', dirs: ['/usr/local/lib'], required: false)
  # 强制 static link: 把 .a 完整路径加到 link_args，绕过 --as-needed
  if boost_thread_dep.found()
    boost_static_link_args += ['/usr/local/lib/libboost_thread.a']
  endif
  if boost_system_dep.found()
    boost_static_link_args += ['/usr/local/lib/libboost_system.a']
  endif
  if boost_chrono_dep.found()
    boost_static_link_args += ['/usr/local/lib/libboost_chrono.a']
  endif
else
  boost_thread_dep = dependency('', required: false)
  boost_system_dep = dependency('', required: false)
  boost_chrono_dep = dependency('', required: false)
endif
if host_machine.system() == 'windows' and not boost_python_dep.found()
  # 优先使用 conda-forge 环境变量 (BOOST_INCLUDE / BOOST_LIB)
  boost_inc_env = run_command('python', '-c', 'import os; print(os.environ.get("BOOST_INCLUDE",""))', check: false).stdout().strip()
  boost_lib_env = run_command('python', '-c', 'import os; print(os.environ.get("BOOST_LIB",""))', check: false).stdout().strip()

  # 回退到 BOOST_ROOT
  if boost_inc_env == ''
    boost_root = run_command('python', '-c', 'import os; print(os.environ.get("BOOST_ROOT",""))', check: false).stdout().strip()
    if boost_root != ''
      boost_inc_env = boost_root / 'include'
      boost_lib_env = boost_root / 'lib'
    endif
  endif

  # 如果找到了额外 include 路径，追加到 include_dirs
  if boost_inc_env != '' and fs.is_dir(boost_inc_env)
    boost_inc_dir = include_directories(boost_inc_env)
    include_dirs = [include_dirs, boost_inc_dir]
    message('Using Boost include from: ' + boost_inc_env)
    if boost_lib_env != ''
      foreach name : boost_python_lib_names
        if not boost_python_dep.found()
          boost_python_dep = cpp.find_library(name, dirs: [boost_lib_env], required: false)
        endif
      endforeach
      # Also find boost_thread, boost_system, boost_chrono for static link
      boost_thread_names = [
        'boost_thread',                                    # CMake system layout
        'boost_thread-vc143-mt-s-x64-1_84',
        'boost_thread-vc143-mt-s64-1_84',
      ]
      boost_system_names = [
        'boost_system',                                    # CMake system layout
        'boost_system-vc143-mt-s-x64-1_84',
        'boost_system-vc143-mt-s64-1_84',
      ]
      boost_chrono_names = [
        'boost_chrono',                                    # CMake system layout
        'boost_chrono-vc143-mt-s-x64-1_84',
        'boost_chrono-vc143-mt-s64-1_84',
      ]
      foreach name : boost_thread_names
        if not boost_thread_dep.found()
          boost_thread_dep = cpp.find_library(name, dirs: [boost_lib_env], required: false)
        endif
      endforeach
      foreach name : boost_system_names
        if not boost_system_dep.found()
          boost_system_dep = cpp.find_library(name, dirs: [boost_lib_env], required: false)
        endif
      endforeach
      foreach name : boost_chrono_names
        if not boost_chrono_dep.found()
          boost_chrono_dep = cpp.find_library(name, dirs: [boost_lib_env], required: false)
        endif
      endforeach
    endif
  endif
endif

if not boost_python_dep.found()
  error('Boost.Python not found! XTP Pro bindings require Boost.Python. ' +
        'Install: b2 --with-python variant=release link=shared install')
endif

message('Using Boost.Python: ' + boost_python_dep.name())

# ---- 编译选项 ----
extra_cpp_args += ['-DUSE_64BITS', '-DBUILD_XTP_MD', '-DBUILD_XTP_TD']

# ---- 行情绑定 (vnxtpxquote) ----
md_link_args = extra_link_args
if host_machine.system() != 'windows'
  md_link_args += ['-L' + lib_dir, '-lxtpxquoteapi', '-Wl,-rpath,$ORIGIN']
  md_link_args += boost_static_link_args
endif

md_module = py.extension_module(
  'vnxtpxquote',
  sources: ['vnpy_xtppro/api/source/vnxtpxquote.cpp'],
  include_directories: include_dirs,
  dependencies: [py_dep, boost_python_dep, xtpx_quote_lib],
  cpp_args: extra_cpp_args,
  link_args: md_link_args,
  install: true,
  install_rpath: install_rpath_value,
  subdir: 'vnpy_xtppro/api',
)

# ---- 交易绑定 (vnxtpxtrader) ----
td_link_args = extra_link_args
if host_machine.system() != 'windows'
  td_link_args += ['-L' + lib_dir, '-lxtpxtraderapi', '-Wl,-rpath,$ORIGIN']
  td_link_args += boost_static_link_args
endif

td_module = py.extension_module(
  'vnxtpxtrader',
  sources: ['vnpy_xtppro/api/source/vnxtpxtrader.cpp'],
  include_directories: include_dirs,
  dependencies: [py_dep, boost_python_dep, xtpx_trader_lib],
  cpp_args: extra_cpp_args,
  link_args: td_link_args,
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
  install_data(api_libs, install_dir: py.get_install_dir() / 'vnpy_xtppro/api')
else
  api_libs = [
    'vnpy_xtppro/api/libs/linux_x86_64/libxtpxquoteapi.so',
    'vnpy_xtppro/api/libs/linux_x86_64/libxtpxtraderapi.so',
  ]
  install_data(api_libs, install_dir: py.get_install_dir() / 'vnpy_xtppro/api')
endif
