# vnpy_xtppro

VeighNa 网关插件 — 中泰证券 XTP Pro 行情接口

## 特性

- **XTP Pro 行情接入**：深度行情快照、逐笔（TBT 可扩展）、合约查询，支持沪/深/北交所
- **Tick → Bar 合成**：子进程内自动将 tick 合成分钟线，推送 `EVENT_BAR` 事件
- **缺失 Bar 补充**：断连期间缺失的分钟线自动补充零量 bar（`volume=0`），仅在同一交易时段内补充
- **多进程分片架构**：每个 worker 进程独立运行 XTP Pro API，可配置订阅容量（默认 500/进程），满则自动起新进程
- **合约批量推送**：`connect()` 等待合约查询完成后才返回，`on_contract` 在 `subscribe` 之前批量触发
- **断线自动重连**：`on_disconnected` → 延迟（≥心跳间隔）→ 重连 → 重查合约 → TCP 重订阅（UDP 组播无需重订阅）
- **自包含 SDK**：XTP Pro C++ SDK (.so/.dll) 内置于 `api/libs/`，编译即用
- **多平台构建**：meson-python 编译体系，GitHub Actions 自动出 wheel（Linux x86_64 + Windows 64-bit，Python 3.10/3.11/3.12）

## 安装

### 预编译 wheel（推荐）

```bash
pip install vnpy_xtppro
```

### 从源码编译

需要 Boost.Python 库和 meson 构建系统：

```bash
# Linux: 安装 Boost.Python
sudo apt install libboost-python3.10-dev libboost-thread-dev libboost-system-dev

# 编译安装
pip install -e .
```

`pip install` 会自动通过 meson-python 编译 C++ 绑定，无需手动 meson/ninja。

## 快速开始

### 无 UI 脚本

```bash
# 填入你的 XTP Pro 账号密码后运行
python script/run.py
```

脚本内容（关键部分）：

```python
from vnpy.event import EventEngine, Event
from vnpy.trader.engine import MainEngine
from vnpy.trader.object import SubscribeRequest
from vnpy.trader.constant import Exchange

from vnpy_xtppro import XtpProGateway
from vnpy_xtppro.gateway import EVENT_BAR

# 1. 创建引擎
event_engine = EventEngine()
main_engine = MainEngine(event_engine)
main_engine.add_gateway(XtpProGateway)

# 2. 注册事件回调
def on_bar(event: Event):
    bar = event.data
    print(f"BAR {bar.vt_symbol} {bar.datetime} C={bar.close_price} V={bar.volume}")

# 收所有合约的 bar
event_engine.register(EVENT_BAR, on_bar)

# 只收 600000.SSE 的 bar
event_engine.register(EVENT_BAR + "600000.SSE", on_bar)

# 3. 连接（等待合约查询完成后返回）
main_engine.connect(setting, "XTP_PRO")

# 4. 订阅
req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
main_engine.subscribe(req, "XTP_PRO")

# 5. 启动
event_engine.start()
```

### VeighNa UI 中使用

```python
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_xtppro import XtpProGateway

qapp = create_qapp()
event_engine = EventEngine()
main_engine = MainEngine(event_engine)
main_engine.add_gateway(XtpProGateway)

main_window = MainWindow(main_engine, event_engine)
main_window.showMaximized()
qapp.exec()
```

## 配置

在 VeighNa 中添加 `XTP_PRO` 网关，配置以下参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 用户名 | XTP Pro 账号 | — |
| 密码 | XTP Pro 密码 | — |
| 客户端ID | 客户端 ID（0-99） | 1 |
| 行情服务器 | MD 服务器地址 | 119.3.103.38 |
| 行情端口 | MD 服务器端口 | 3002 |
| 通讯协议 | TCP / UDP | TCP |
| 日志级别 | TRACE/DEBUG/INFO/WARNING/ERROR/FATAL | INFO |
| 配置文件 | quote_config.ini 路径（UDP 实盘必填） | — |
| 心跳间隔 | 心跳包间隔（秒） | 15 |
| 本地网卡IP | 本机网卡 IP（留空自动获取） | — |
| 每进程订阅数 | 单 worker 进程订阅容量上限 | 500 |

## 事件

### 事件类型

| 事件类型 | 值 | 说明 |
|----------|----|------|
| `EVENT_TICK` | `"eTick."` | VeighNa 核心事件，原始行情推送（TickData） |
| `EVENT_BAR` | `"eBar."` | 分钟线推送（BarData），由 tick 合成 |
| `EVENT_CONTRACT` | `"eContract."` | VeighNa 核心事件，合约信息推送 |
| `EVENT_LOG` | `"eLog"` | VeighNa 核心事件，日志推送 |

### 事件注册方式

与 VeighNa 核心一致，`on_tick` 和 `on_bar` 均推送**两个**事件：

```python
# 通用事件 — 收到所有合约
event_engine.register(EVENT_TICK, on_tick)          # 所有 tick
event_engine.register(EVENT_BAR, on_bar)            # 所有 bar

# 特定合约事件 — 只收到指定合约
event_engine.register(EVENT_TICK + "600000.SSE", on_tick)   # 只收 600000
event_engine.register(EVENT_BAR + "600000.SSE", on_bar)     # 只收 600000
```

> **原理**：`on_tick` 调用 `BaseGateway.on_event(EVENT_TICK, tick)` + `on_event(EVENT_TICK + tick.vt_symbol, tick)`，`on_bar` 同理。

### EVENT_BAR 导入

```python
from vnpy_xtppro.gateway import EVENT_BAR   # "eBar."
```

## Bar 合成

网关在子进程中自动将 tick 合成分钟线：

- 每个 worker 进程内置 `BarGenerator`，`on_tick` → `BarGenerator.update_tick()` → `on_bar`
- 分钟线切换点：tick 的 `datetime.minute` 变化时推送上一根 bar
- 支持多合约并行，每个 `vt_symbol` 独立状态

### 缺失 Bar 补充

断连后重连，中间缺失的分钟线自动补充：

- 补充 bar 的 OHLC = 上一根 bar 的收盘价，volume = 0，turnover = 0
- 仅在同一交易时段内补充（早盘 9:30-11:30、午盘 13:00-15:00）
- 跨时段（早盘→午盘、昨日→今日）不补充
- 补充 bar 和正常 bar 统一走 `on_bar`，通过 `volume=0` 区分

## 多进程分片

当订阅数量超过单进程容量（默认 500），网关自动起新 worker 进程：

- 每个 worker 独立运行 XtpProMdApi，有独立的 command_queue
- tick_queue / bar_queue / log_queue 跨进程共享
- 已退出进程的订阅自动回收，slot 可复用

## 重连机制

```
on_disconnected(reason)
  → 等待 max(5s, heartbeat_interval)
  → 重新 login
  → 重新查询合约（逐市场，等 is_last）
  → TCP: 重新订阅所有合约
  → UDP: 组播不受影响，跳过重订阅
```

## 项目结构

```
vnpy_xtppro/
├── vnpy_xtppro/
│   ├── __init__.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── xtp_pro_md_api.py       # 行情 API Python 封装（XtpProMdApi）
│   │   ├── include/                # XTP Pro C++ SDK 头文件
│   │   ├── source/                 # Boost.Python 绑定源码
│   │   │   ├── vnxtpxquote.cpp/h   # 行情绑定
│   │   │   └── vnxtpxtrader.cpp/h  # 交易绑定（暂未使用）
│   │   └── libs/                   # 预编译 SDK 库
│   │       ├── linux_x86_64/       # libxtpxquoteapi.so, libxtpxtraderapi.so
│   │       └── win64/              # xtpxquoteapi.dll/.lib, xtpxtraderapi.dll/.lib
│   ├── gateway/
│   │   ├── __init__.py
│   │   └── xtp_pro_gateway.py      # 网关主类 + BarGenerator + worker
│   └── etc/
│       └── xtppro_md_template.ini  # UDP 配置文件模板
├── script/
│   └── run.py                      # 无 UI 启动脚本
├── test/
│   ├── test_bar_generator.py        # Bar 合成器单元测试
│   ├── test_unit.py                 # API 封装层单元测试
│   ├── test_mock.py                 # 网关逻辑 mock 测试
│   └── test_integration.py          # 真实行情集成测试
├── meson.build                      # Meson 构建配置
├── pyproject.toml                   # 项目元数据 + meson-python 构建后端
└── .github/workflows/
    └── build_wheels.yml             # CI: 多平台编译 + 测试 + 集成测试
```

## 架构

三层架构，仅 Layer 1 需要编译：

```
┌─────────────────────────────────────────────────┐
│ Layer 3  gateway/xtp_pro_gateway.py  (Python)   │  ← 网关逻辑、BarGenerator、重连
│           改此层无需重新编译                       │
├─────────────────────────────────────────────────┤
│ Layer 2  api/xtp_pro_md_api.py  (Python)        │  ← 继承 vnxtpxquote.QuoteApi，
│           映射 C++ 回调字典 → Python TickData     │  改此层无需重新编译
├─────────────────────────────────────────────────┤
│ Layer 1  vnxtpxquote.so  (C++ 编译)             │  ← Boost.Python 绑定，
│           暴露 QuoteApi + 71 个方法/回调          │  改此层需重新编译
└─────────────────────────────────────────────────┘
```

- **Layer 1** 由 XTP Pro SDK 自带的 `vnxtpxquote.cpp` 生成，71 个 `.def()` 对应 SDK 的 70 个虚函数 + CreateQuoteApi
- **Layer 2/3** 是纯 Python，修改后 `pip install -e .` 即生效

## 测试

### 单元测试（无需 SDK）

```bash
pip install vnpy pytest
python -m pytest test/test_bar_generator.py test/test_unit.py test/test_mock.py -v
```

### 集成测试（真实行情服务器）

需要设置环境变量 `XTP_PRO_TEST_USER` 和 `XTP_PRO_TEST_PASSWORD`：

```bash
export XTP_PRO_TEST_USER=your_account
export XTP_PRO_TEST_PASSWORD=your_password
python -m pytest test/test_integration.py -v -s
```

测试服务器 `119.3.103.38:3002` 可用时段：连通性 9:00-22:00，行情推送 9:00-16:30。

## CI

GitHub Actions 自动执行：

| Job | 说明 |
|-----|------|
| build (Linux/Windows × py3.10/3.11/3.12) | 编译 wheel 并上传 artifact |
| test (py3.10/3.11/3.12) | 纯 Python 单元测试 |
| integration (py3.10, push only) | 安装 wheel + vnpy + ta-lib，连接真实 MD 服务器测试 |

集成测试仅在 push 到 main/master 时触发（PR 不跑，保护密钥），凭证通过 GitHub Secrets 传入。

## 已知限制

- **交易网关**（TdGateway）暂未实现，仅行情
- **北交所**：公网测试环境无北交所行情，实盘 UDP 正常
- **8:50 前订阅**：可能返回错误码 11200404（交易所尚未推送快照）
- **T+1 重启**：实盘需每日重启进程，XTP Pro 不支持跨日

## 与 xtp_pro_api_python 的关系

本项目**不依赖** [xtp_pro_api_python](https://github.com/ztsec/xtp_pro_api_python) 仓库。
- `api/source/` 中的 C++ 绑定源码来自该仓库，但已内置于本项目
- `api/libs/` 中的 SDK 预编译库直接来自 XTP Pro 官方 SDK
- 编译体系使用 meson-python，替代 CMakeLists.txt

## 许可证

MIT License

## 致谢

- [中泰证券 XTP Pro](https://xtp.zts.com.cn/xtp-pro/) — 官方行情接口
- [VeighNa](https://www.veighna.com/) — 量化交易平台
