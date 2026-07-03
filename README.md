# vnpy_xtppro

VeighNa 网关插件 - 中泰证券 XTP Pro 行情接口

## 特性

- **XTP Pro 行情接入**：支持 XTP Pro 全部行情功能（深度行情、逐笔、订单簿、IOPV、港股通等）
- **Tick → Bar 合成**：在独立子进程中完成 tick→分钟线合成，推送 `EVENT_BAR` 事件
- **缺失 Bar 补充**：自动检测断连期间的缺失分钟线，用零量 bar 补充并触发告警回调
- **独立进程架构**：行情 API 运行在子进程中，通过 `multiprocessing.Queue` 通信，不阻塞主进程
- **自包含 SDK**：XTP Pro C++ SDK (.so/.dll) 内置于 `api/libs/`，无需外部依赖
- **多平台编译**：支持 Linux x86_64 + Windows 64 位，提供 meson.build 和 setup.py 双编译体系

## 安装

### 预编译版本（推荐）

```bash
pip install vnpy_xtppro
```

### 从源码编译

编译 C++ 绑定需要 Boost.Python 库：

```bash
# 1. 安装 Boost.Python (Linux)
sudo apt install libboost-python3-dev libboost-thread-dev libboost-system-dev

# 2. 使用 meson 编译
cd vnpy_xtppro/api
meson setup builddir --prefix=/usr/local
ninja -C builddir
ninja -C builddir install

# 3. 安装 Python 包
pip install -e .
```

或使用 setup.py 直接编译（pip install 时自动触发）：

```bash
pip install -e .  # 需要 boost_python3x 共享库在搜索路径中
```

## 使用

### 配置

在 VeighNa 中添加 XTP_PRO 网关，配置以下参数：

| 参数 | 说明 | 示例 |
|------|------|------|
| 用户名 | XTP Pro 账号 | 453191001783 |
| 密码 | XTP Pro 密码 | - |
| 客户号 | 客户端 ID | 1 |
| 行情服务器 | MD 服务器地址 | 122.112.252.150 |
| 行情端口 | MD 服务器端口 | 3002 |
| 协议类型 | TCP=1, UDP=2 | 1 |
| 日志级别 | 0=TRACE,1=DEBUG,2=INFO,3=WARN,4=ERROR,5=FATAL | 2 |
| 配置文件 | XTP Pro 配置文件路径（可选） | - |

### 事件

| 事件类型 | 说明 |
|----------|------|
| `EVENT_TICK` | 原始行情推送（TickData） |
| `EVENT_BAR` | 分钟线推送（BarData），由 tick 合成 |
| `EVENT_CONTRACT` | 合约信息推送 |
| `EVENT_LOG` | 日志推送 |

### Bar 合成

网关在独立子进程中自动将 tick 合成为分钟线：

```python
from vnpy.event.engine import EventEngine
from vnpy_xtppro import XtpProGateway

ee = EventEngine()
gateway = XtpProGateway(ee, "XTP_PRO")
gateway.connect(setting)

# 注册 bar 事件处理
from vnpy_xtppro.gateway.xtp_pro_gateway import EVENT_BAR
from vnpy.event.engine import Event

def on_bar(event: Event):
    bar = event.data
    print(f"Bar: {bar.vt_symbol} {bar.datetime} C={bar.close_price} V={bar.volume}")

ee.register(EVENT_BAR + "600000.SSE", on_bar)
```

### 缺失 Bar 补充

当行情断连后重连，中间缺失的分钟线会自动补充为零量 bar：

- 补充 bar 的 OHLC = 上一根 bar 的收盘价
- 补充 bar 的 volume = 0, turnover = 0
- 同时触发 `on_bar_gap` 回调（用于告警/记录）

## 项目结构

```
vnpy_xtppro/
├── vnpy_xtppro/
│   ├── __init__.py              # 包入口
│   ├── api/
│   │   ├── xtp_pro_md_api.py    # 行情 API Python 封装
│   │   ├── include/             # XTP Pro C++ SDK 头文件
│   │   ├── source/              # Boost.Python 绑定源码
│   │   │   ├── vnxtpxquote.cpp  # 行情绑定
│   │   │   └── vnxtpxtrader.cpp # 交易绑定
│   │   ├── libs/                # 预编译 SDK 库
│   │   │   ├── linux_x86_64/   # .so
│   │   │   └── win64/          # .dll/.lib
│   │   └── meson.build          # Meson 构建配置
│   ├── gateway/
│   │   ├── xtp_pro_gateway.py   # VeighNa 网关主类
│   │   ├── xtp_pro_md.py        # 行情子进程 worker
│   │   └── bar_generator.py     # tick→bar 合成器
│   └── etc/
│       └── xtppro_md_template.ini
├── test/                         # 测试
├── setup.py                      # setuptools 编译入口
├── meson.build                   # 顶层 meson 配置
├── pyproject.toml                # 项目元数据
└── .github/workflows/build.yml  # CI 多平台编译
```

## 编译体系

本项目提供两种编译方式：

### 1. setup.py (setuptools Extension)

`pip install` 时自动编译 C++ 绑定，类似 `vnpy_ctp` 模式。需要 Boost.Python 在库搜索路径中。

### 2. meson.build

更灵活的编译方式，支持交叉编译和精细控制：

```bash
meson setup builddir --prefix=/usr/local
ninja -C builddir
ninja -C builddir install
```

### 3. GitHub Actions CI

自动在 Linux x86_64 和 Windows 64 位上编译，支持 Python 3.10/3.11/3.12。

## 与 xtp_pro_api_python 的关系

本项目**不依赖** [xtp_pro_api_python](https://github.com/ztsec/xtp_pro_api_python) 仓库。
- `api/source/` 中的 C++ 绑定源码来自该仓库，但已内置于本项目
- `api/libs/` 中的 SDK 预编译库直接来自 XTP Pro 官方 SDK
- 编译体系使用 meson.build / setup.py，替代 CMakeLists.txt

## 许可证

MIT License

## 致谢

- [中泰证券 XTP Pro](https://xtp.zts.com.cn/xtp-pro/) - 官方行情接口
- [VeighNa](https://www.veighna.com/) - 量化交易平台
