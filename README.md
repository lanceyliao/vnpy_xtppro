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
sudo apt install libboost-python310-dev libboost-thread-dev libboost-system-dev

# 编译安装
pip install -e .
```

`pip install` 会自动通过 meson-python 编译 C++ 绑定，无需手动 meson/ninja。

## 使用

### 配置

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

### 事件

| 事件类型 | 说明 |
|----------|------|
| `EVENT_TICK` | 原始行情推送（TickData） |
| `EVENT_BAR` | 分钟线推送（BarData），由 tick 合成，topic = `eBar.` + vt_symbol |
| `EVENT_CONTRACT` | 合约信息推送（connect 时批量触发） |
| `EVENT_LOG` | 日志推送 |

### Bar 合成

网关在子进程中自动将 tick 合成分钟线：

```python
from vnpy.event.engine import EventEngine, Event
from vnpy_xtppro.gateway import XtpProGateway
from vnpy_xtppro.gateway.xtp_pro_gateway import EVENT_BAR

ee = EventEngine()
gateway = XtpProGateway(ee, "XTP_PRO")
gateway.connect(setting)  # 等待合约查询完成后返回

# 订阅
from vnpy.trader.object import SubscribeRequest
from vnpy.trader.constant import Exchange
req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
gateway.subscribe(req)

# 注册 bar 事件处理
def on_bar(event: Event):
    bar = event.data
    print(f"Bar: {bar.vt_symbol} {bar.datetime} C={bar.close_price} V={bar.volume}")

ee.register(EVENT_BAR + "600000.SSE", on_bar)
ee.start()
```

### 缺失 Bar 补充

断连后重连，中间缺失的分钟线自动补充：

- 补充 bar 的 OHLC = 上一根 bar 的收盘价，volume = 0，turnover = 0
- 仅在同一交易时段内补充（早盘 9:30-11:30、午盘 13:00-15:00）
- 跨时段（早盘→午盘、昨日→今日）不补充
- 补充 bar 和正常 bar 统一走 `on_bar`，通过 `volume=0` 区分

### 多进程分片

当订阅数量超过单进程容量（默认 500），网关自动起新 worker 进程：

- 每个 worker 独立运行 XtpProMdApi，有独立的 command_queue
- tick_queue / bar_queue / log_queue 跨进程共享
- 已退出进程的订阅自动回收，slot 可复用

### 重连机制

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
├── test/
│   ├── test_bar_generator.py        # Bar 合成器单元测试
│   ├── test_unit.py                 # API 封装层单元测试
│   ├── test_mock.py                 # 网关逻辑 mock 测试
│   └── test_integration.py          # 真实行情集成测试（连接测试服务器）
├── meson.build                      # Meson 构建配置
├── pyproject.toml                   # 项目元数据 + meson-python 构建后端
├── setup.py                         # setuptools 兼容入口
└── .github/workflows/
    └── build_wheels.yml             # CI: 多平台编译 + 测试 + 集成测试
```

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
| Build (Linux/Windows × py3.10/3.11/3.12) | 编译 wheel 并上传 artifact |
| Test (py3.10/3.11/3.12) | 纯 Python 单元测试 |
| Integration (py3.10, push only) | 安装 wheel + vnpy + ta-lib，连接真实 MD 服务器测试 |

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
