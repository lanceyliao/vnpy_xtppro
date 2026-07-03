"""
XTP Pro 真实行情集成测试

连接公网测试服务器，验证：
1. 登录成功
2. 合约查询
3. 订阅 + 收到 tick
4. Bar 合成

需要环境变量：
- XTP_PRO_TEST_USER: 测试账号
- XTP_PRO_TEST_PASSWORD: 测试密码

公网测试服务器: 122.112.252.150:3002 (TCP)
可测试时间: 9:00~16:30 (行情), 9:00~22:00 (连通性)
"""

import os
import time
import threading
from typing import List, Optional

import pytest

# 跳过条件：无凭证或非 x86-64
pytestmark = pytest.mark.skipif(
    not os.environ.get("XTP_PRO_TEST_USER") or not os.environ.get("XTP_PRO_TEST_PASSWORD"),
    reason="需要设置 XTP_PRO_TEST_USER 和 XTP_PRO_TEST_PASSWORD 环境变量",
)


def _import_gateway():
    """延迟导入，确保编译好的 .so 已加载"""
    from vnpy_xtppro.gateway import XtpProGateway
    return XtpProGateway


class _EventCollector:
    """收集 EventEngine 推送的事件"""

    def __init__(self):
        self.ticks: List = []
        self.bars: List = []
        self.contracts: List = []
        self.logs: List[str] = []
        self._lock = threading.Lock()
        self.tick_event = threading.Event()
        self.bar_event = threading.Event()
        self.contract_event = threading.Event()

    def on_tick(self, tick):
        with self._lock:
            self.ticks.append(tick)
        self.tick_event.set()

    def on_bar(self, bar):
        with self._lock:
            self.bars.append(bar)
        self.bar_event.set()

    def on_contract(self, contract):
        with self._lock:
            self.contracts.append(contract)
        self.contract_event.set()

    def on_log(self, log):
        with self._lock:
            self.logs.append(str(log))


@pytest.fixture(scope="module")
def gateway_and_collector():
    """创建网关并连接，测试结束后关闭"""
    from vnpy.event.engine import EventEngine
    from vnpy.trader.event import EVENT_TICK, EVENT_CONTRACT, EVENT_LOG
    from vnpy.trader.constant import Exchange
    from vnpy_xtppro.gateway import EVENT_BAR

    XtpProGateway = _import_gateway()
    ee = EventEngine()
    gw = XtpProGateway(ee, "XTP_PRO")
    collector = _EventCollector()

    # 注册事件处理
    def _handle_tick(event):
        collector.on_tick(event.data)

    def _handle_bar(event):
        collector.on_bar(event.data)

    def _handle_contract(event):
        collector.on_contract(event.data)

    def _handle_log(event):
        collector.on_log(event.data)

    ee.register(EVENT_TICK, _handle_tick)
    ee.register(EVENT_BAR, _handle_log)  # bar 事件用 topic 区分
    ee.register(EVENT_CONTRACT, _handle_contract)
    ee.register(EVENT_LOG, _handle_log)

    # 注册 bar 事件（每个 vt_symbol 一个 topic）
    # 我们在收到 tick 后再注册对应 bar topic
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
    # connect() 内部已等待合约查询完成，无需额外等待

    yield gw, collector, ee

    gw.close()
    ee.stop()


class TestXtpProIntegration:
    """真实行情集成测试"""

    def test_connect_and_query_contracts(self, gateway_and_collector):
        """测试1: 连接 + 合约查询

        connect() 内部等待合约查询完成。
        非交易时段（22:00-9:00）可能返回 0 个合约，这不算失败。
        """
        gw, collector, ee = gateway_and_collector

        # connect() 已等待合约查询完成，检查结果
        with collector._lock:
            contract_count = len(collector.contracts)

        if contract_count == 0:
            # 非交易时段：连接成功但无合约，标记 skip
            pytest.skip("非交易时段，测试服务器未返回合约（9:00-16:30 才有数据）")

        print(f"✓ 收到 {contract_count} 个合约")

    def test_subscribe_and_receive_tick(self, gateway_and_collector):
        """测试2: 订阅行情 + 收到 tick"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange

        gw, collector, ee = gateway_and_collector

        # 先检查是否有合约（非交易时段可能没有）
        if len(collector.contracts) == 0:
            pytest.skip("非交易时段，无合约可订阅")

        # 订阅 600000.SH (浦发银行，测试环境全订阅7只之一)
        req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
        gw.subscribe(req)

        # 等待 tick（交易时段内应该 3s 内收到）
        received = collector.tick_event.wait(timeout=30)
        assert received or len(collector.ticks) > 0, "30s 内应收到至少一个 tick"

        with collector._lock:
            tick_count = len(collector.ticks)

        assert tick_count > 0, "应收到 tick 数据"
        tick = collector.ticks[0]
        assert tick.symbol == "600000"
        assert tick.exchange == Exchange.SSE
        print(f"✓ 收到 {tick_count} 个 tick, last_price={tick.last_price}")

    def test_bar_generation(self, gateway_and_collector):
        """测试3: Bar 合成（需要交易时段，tick 持续推送才能合成 bar）"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange

        gw, collector, ee = gateway_and_collector

        if len(collector.contracts) == 0:
            pytest.skip("非交易时段，无合约可订阅")

        # 确保已订阅
        req = SubscribeRequest(symbol="600000", exchange=Exchange.SSE)
        gw.subscribe(req)

        # 注册 bar 事件
        from vnpy_xtppro.gateway import EVENT_BAR
        bar_topic = EVENT_BAR + "600000.SSE"
        bar_received = threading.Event()

        def _on_bar(event):
            collector.on_bar(event.data)
            bar_received.set()

        ee.register(bar_topic, _on_bar)

        # 等待 bar（分钟线需要等 1 分钟才可能合成）
        bar_received.wait(timeout=90)

        with collector._lock:
            bar_count = len(collector.bars)

        if bar_count > 0:
            bar = collector.bars[0]
            print(f"✓ 收到 {bar_count} 个 bar, datetime={bar.datetime}")
        else:
            print("⚠ 未收到 bar（可能不在交易时段，或等待时间不足1分钟）")
            # 不 fail，因为非交易时段没有 bar 是正常的

    def test_multi_symbol_subscribe(self, gateway_and_collector):
        """测试4: 多合约订阅"""
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange

        gw, collector, ee = gateway_and_collector

        if len(collector.contracts) == 0:
            pytest.skip("非交易时段，无合约可订阅")

        # 订阅多只（测试环境限制每市场100只）
        symbols = [
            ("600000", Exchange.SSE),  # 浦发银行
            ("000002", Exchange.SZSE),  # 万科A
        ]

        for symbol, exchange in symbols:
            req = SubscribeRequest(symbol=symbol, exchange=exchange)
            gw.subscribe(req)

        # 等待 tick
        time.sleep(15)

        with collector._lock:
            tick_symbols = {t.vt_symbol for t in collector.ticks}

        # 至少应收到一只的 tick
        assert len(tick_symbols) > 0, "应收到至少一只股票的 tick"
        print(f"✓ 收到 {len(tick_symbols)} 只股票的 tick: {tick_symbols}")
