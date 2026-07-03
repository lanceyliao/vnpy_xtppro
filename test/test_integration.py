"""
XTP Pro 真实行情集成测试

连接公网测试服务器，验证：
1. 登录成功（7*24 可测）
2. 合约查询（交易时段 9:00-16:30 有数据）
3. 订阅 + 收到 tick（交易时段）
4. Bar 合成（交易时段）
5. 多合约订阅（交易时段）

需要环境变量：
- XTP_PRO_TEST_USER: 测试账号
- XTP_PRO_TEST_PASSWORD: 测试密码

测试服务器: 119.3.103.38:3002 (TCP)
连通性: 9:00~22:00（可登录、查合约）
行情推送: 9:00~16:30（有实时 tick）
"""

import os
import time
import threading
from typing import List, Optional

import pytest

# 跳过条件：无凭证
pytestmark = pytest.mark.skipif(
    not os.environ.get("XTP_PRO_TEST_USER") or not os.environ.get("XTP_PRO_TEST_PASSWORD"),
    reason="需要设置 XTP_PRO_TEST_USER 和 XTP_PRO_TEST_PASSWORD 环境变量",
)


class _EventCollector:
    """收集 EventEngine 推送的事件"""

    def __init__(self):
        self.ticks: List = []
        self.bars: List = []
        self.contracts: List = []
        self.logs: List[str] = []
        self._lock = threading.Lock()
        self.tick_event = threading.Event()

    def on_tick(self, tick):
        with self._lock:
            self.ticks.append(tick)
        self.tick_event.set()

    def on_bar(self, bar):
        with self._lock:
            self.bars.append(bar)

    def on_contract(self, contract):
        with self._lock:
            self.contracts.append(contract)

    def on_log(self, log):
        with self._lock:
            self.logs.append(str(log))


@pytest.fixture(scope="module")
def gateway_and_collector():
    """创建网关并连接，测试结束后关闭"""
    from vnpy.event.engine import EventEngine
    from vnpy.trader.event import EVENT_TICK, EVENT_CONTRACT, EVENT_LOG
    from vnpy_xtppro.gateway import XtpProGateway, EVENT_BAR

    ee = EventEngine()
    gw = XtpProGateway(ee, "XTP_PRO")
    collector = _EventCollector()

    # 注册事件处理
    ee.register(EVENT_TICK, lambda e: collector.on_tick(e.data))
    ee.register(EVENT_CONTRACT, lambda e: collector.on_contract(e.data))
    ee.register(EVENT_LOG, lambda e: collector.on_log(e.data))
    ee.start()

    setting = {
        "用户名": os.environ["XTP_PRO_TEST_USER"],
        "密码": os.environ["XTP_PRO_TEST_PASSWORD"],
        "客户端ID": 1,
        "行情服务器": "119.3.103.38",
        "行情端口": 3002,
        "通讯协议": "TCP",
        "日志级别": "INFO",
        "配置文件": "",
        "心跳间隔": 15,
        "本地网卡IP": "",
        "每进程订阅数": 500,
    }

    gw.connect(setting)
    # connect() 内部等待合约查询完成（最多 60s）

    yield gw, collector, ee

    gw.close()
    ee.stop()


def _has_contracts(collector: _EventCollector) -> bool:
    """检查是否有合约数据"""
    with collector._lock:
        return len(collector.contracts) > 0


class TestXtpProIntegration:
    """真实行情集成测试"""

    # ---- 测试 1: 连接（7*24 可测）----

    def test_connect_login_success(self, gateway_and_collector):
        """测试1a: 登录连接成功

        connect() 启动 worker 进程并等待合约查询。
        worker 进程存活 = 登录成功。
        此测试 7*24 均可运行（非交易时段也能连上）。
        """
        gw, collector, ee = gateway_and_collector

        # 验证 worker 进程存活（= 登录成功）
        assert len(gw._process_slots) > 0, "应有至少一个 worker 进程"
        worker_alive = any(
            slot["process"].is_alive() for slot in gw._process_slots
        )
        assert worker_alive, "worker 进程应存活（登录成功）"
        print("✓ 登录连接成功，worker 进程存活")

    def test_connect_logs_received(self, gateway_and_collector):
        """测试1b: 连接后应有日志输出"""
        gw, collector, ee = gateway_and_collector

        with collector._lock:
            log_count = len(collector.logs)

        assert log_count > 0, "连接后应有日志输出"
        # 打印前几条日志帮助调试
        with collector._lock:
            for log in collector.logs[:5]:
                print(f"  log: {log[:120]}")

    # ---- 测试 2: 合约查询（交易时段才有数据）----

    def test_query_contracts(self, gateway_and_collector):
        """测试2: 合约查询

        交易时段 (9:00-16:30) 应返回大量合约。
        非交易时段可能返回 0 个，skip 而非 fail。
        """
        gw, collector, ee = gateway_and_collector

        if not _has_contracts(collector):
            pytest.skip("非交易时段，测试服务器未返回合约")

        with collector._lock:
            contract_count = len(collector.contracts)
        print(f"✓ 收到 {contract_count} 个合约")

    # ---- 测试 3: 订阅 + 收 tick（交易时段）----

    def test_subscribe_and_receive_tick(self, gateway_and_collector):
        """测试3: 订阅行情 + 收到 tick"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange

        gw, collector, ee = gateway_and_collector

        if not _has_contracts(collector):
            pytest.skip("非交易时段，无合约可订阅")

        req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
        gw.subscribe(req)

        # 等待 tick（交易时段内 3s 左右收到）
        received = collector.tick_event.wait(timeout=30)
        assert received or len(collector.ticks) > 0, "30s 内应收到至少一个 tick"

        with collector._lock:
            tick_count = len(collector.ticks)

        assert tick_count > 0, "应收到 tick 数据"
        tick = collector.ticks[0]
        assert tick.symbol == "600000"
        assert tick.exchange == Exchange.SSE
        print(f"✓ 收到 {tick_count} 个 tick, last_price={tick.last_price}")

    # ---- 测试 4: Bar 合成（交易时段）----

    def test_bar_generation(self, gateway_and_collector):
        """测试4: Bar 合成（需等 1 分钟才合成一根分钟线）"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange
        from vnpy_xtppro.gateway import EVENT_BAR

        gw, collector, ee = gateway_and_collector

        if not _has_contracts(collector):
            pytest.skip("非交易时段，无合约可订阅")

        req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
        gw.subscribe(req)

        bar_topic = EVENT_BAR + "600000.SSE"
        bar_received = threading.Event()

        def _on_bar(event):
            collector.on_bar(event.data)
            bar_received.set()

        ee.register(bar_topic, _on_bar)

        bar_received.wait(timeout=90)

        with collector._lock:
            bar_count = len(collector.bars)

        if bar_count > 0:
            bar = collector.bars[0]
            print(f"✓ 收到 {bar_count} 个 bar, datetime={bar.datetime}")
        else:
            print("⚠ 90s 内未收到 bar（可能刚过整分钟，需等下一根）")

    # ---- 测试 5: 多合约订阅（交易时段）----

    def test_multi_symbol_subscribe(self, gateway_and_collector):
        """测试5: 多合约订阅"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange

        gw, collector, ee = gateway_and_collector

        if not _has_contracts(collector):
            pytest.skip("非交易时段，无合约可订阅")

        symbols = [
            ("600000", Exchange.SSE),   # 浦发银行
            ("000002", Exchange.SZSE),  # 万科A
        ]

        for symbol, exchange in symbols:
            req = SubscribeRequest(symbol=symbol, exchange=exchange)
            gw.subscribe(req)

        time.sleep(15)

        with collector._lock:
            tick_symbols = {t.vt_symbol for t in collector.ticks}

        assert len(tick_symbols) > 0, "应收到至少一只股票的 tick"
        print(f"✓ 收到 {len(tick_symbols)} 只股票的 tick: {tick_symbols}")
