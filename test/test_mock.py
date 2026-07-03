"""
XTP Pro 行情网关 Mock 测试

在没有 XTP Pro C++ SDK 的环境下，使用 mock 验证网关逻辑。
"""

import multiprocessing as mp
import queue
import threading
import time
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vnpy.trader.constant import Exchange, Product
from vnpy.trader.object import TickData, ContractData, SubscribeRequest

from vnpy_xtppro.api.xtp_pro_md_api import XtpProMdApi
from vnpy_xtppro.gateway.xtp_pro_gateway import (
    EXCHANGE_XTP2VT,
    EXCHANGE_VT2XTP,
    PRODUCT_XTP2VT,
    PROTOCOL_VT2XTP,
    LOGLEVEL_VT2XTP,
    CMD_SUBSCRIBE,
    CMD_UNSUBSCRIBE,
    CMD_STOP,
    _on_depth_market_data,
    _on_query_all_tickers,
    symbol_contract_map,
    _normalize_queue_exception,
    _drain_commands,
)
from vnpy_xtppro.gateway.xtp_pro_gateway import XtpProGateway, get_default_setting


# ------------------------------------------------------------------
# 1. API 封装层测试
# ------------------------------------------------------------------

def test_api_init():
    """测试 API 初始状态"""
    api = XtpProMdApi()
    assert api._api is None
    assert api._active is False
    assert api.connect_status is False
    assert api.login_status is False
    print("✓ API 初始状态正确")


def test_api_callbacks():
    """测试回调函数设置"""
    api = XtpProMdApi()
    callbacks = [
        "on_disconnected",
        "on_error",
        "on_sub_market_data",
        "on_depth_market_data",
        "on_query_all_tickers",
        "on_tick_by_tick",
    ]
    for name in callbacks:
        assert getattr(api, name) is None

    # 设置回调
    api.on_disconnected = lambda r: None
    api.on_error = lambda e: None
    assert api.on_disconnected is not None
    print("✓ API 回调函数设置正确")


def test_api_disconnected_callback():
    """测试断开连接回调"""
    api = XtpProMdApi()
    api.connect_status = True
    api.login_status = True

    reasons = []
    api.on_disconnected = lambda r: reasons.append(r)

    api.onDisconnected(0)
    assert not api.connect_status
    assert not api.login_status
    assert reasons == [0]
    print("✓ 断开连接回调正确重置状态")


def test_api_subscribe_without_login():
    """测试未登录时订阅返回 -1"""
    api = XtpProMdApi()
    assert api.subscribe_market_data("600000", 1) == -1
    assert api.unsubscribe_market_data("600000", 1) == -1
    assert api.subscribe_all_market_data() == -1
    assert api.subscribe_tick_by_tick("600000", 1) == -1
    assert api.query_all_tickers(1) == -1
    print("✓ 未登录时订阅/查询返回 -1")


# ------------------------------------------------------------------
# 2. 映射常量测试
# ------------------------------------------------------------------

def test_exchange_mapping():
    """测试交易所双向映射"""
    assert EXCHANGE_XTP2VT[1] == Exchange.SSE
    assert EXCHANGE_XTP2VT[2] == Exchange.SZSE
    assert EXCHANGE_XTP2VT[3] == Exchange.BSE  # 北交所
    assert EXCHANGE_VT2XTP[Exchange.SSE] == 1
    assert EXCHANGE_VT2XTP[Exchange.SZSE] == 2
    assert EXCHANGE_VT2XTP[Exchange.BSE] == 3
    print("✓ 交易所映射正确（含北交所）")


def test_product_mapping():
    """测试产品类型映射"""
    assert PRODUCT_XTP2VT[0] == Product.EQUITY
    assert PRODUCT_XTP2VT[1] == Product.INDEX
    assert PRODUCT_XTP2VT[2] == Product.FUND
    assert PRODUCT_XTP2VT[3] == Product.BOND
    assert PRODUCT_XTP2VT[4] == Product.OPTION
    print("✓ 产品类型映射正确")


def test_protocol_loglevel_mapping():
    """测试协议和日志级别映射"""
    assert PROTOCOL_VT2XTP["TCP"] == 1
    assert PROTOCOL_VT2XTP["UDP"] == 2
    assert LOGLEVEL_VT2XTP["INFO"] == 3
    assert LOGLEVEL_VT2XTP["DEBUG"] == 4
    print("✓ 协议和日志级别映射正确")


# ------------------------------------------------------------------
# 3. 行情数据处理测试
# ------------------------------------------------------------------

def test_depth_market_data_conversion():
    """测试深度行情数据转换"""
    tick_queue = mp.Queue()

    data = {
        "ticker": "600000",
        "exchange_id": 1,
        "data_time": 20230703100000123000,  # 2023-07-03 10:00:00.123
        "last_price": 10.05,
        "qty": 1000000,
        "turnover": 10050000.0,
        "upper_limit_price": 11.0,
        "lower_limit_price": 9.0,
        "open_price": 10.0,
        "high_price": 10.1,
        "low_price": 9.95,
        "pre_close_price": 10.0,
        "bid": [10.04, 10.03, 10.02, 10.01, 10.00],
        "ask": [10.05, 10.06, 10.07, 10.08, 10.09],
        "bid_qty": [100, 200, 300, 400, 500],
        "ask_qty": [150, 250, 350, 450, 550],
    }

    _on_depth_market_data(
        data,
        [], 0, 0,  # bid1_qty_list
        [], 0, 0,  # ask1_qty_list
        tick_queue,
        None,  # bar_gen (not testing bar synthesis here)
    )

    tick = tick_queue.get(timeout=2)
    assert isinstance(tick, TickData)
    assert tick.symbol == "600000"
    assert tick.exchange == Exchange.SSE
    assert tick.last_price == 10.05
    assert tick.volume == 1000000
    assert tick.bid_price_1 == 10.04
    assert tick.ask_price_1 == 10.05
    assert tick.bid_volume_1 == 100
    assert tick.ask_volume_1 == 150
    print("✓ 深度行情数据转换正确")


def test_depth_market_data_with_pricetick():
    """测试带 pricetick 的行情四舍五入"""
    tick_queue = mp.Queue()

    # 预先设置合约信息
    contract = ContractData(
        symbol="600000",
        exchange=Exchange.SSE,
        name="浦发银行",
        product=Product.EQUITY,
        size=100,
        pricetick=0.01,
        min_volume=1,
        gateway_name="XTP_PRO",
    )
    symbol_contract_map["600000.SSE"] = contract

    data = {
        "ticker": "600000",
        "exchange_id": 1,
        "data_time": 20230703100000123000,
        "last_price": 10.055,  # 应四舍五入到 10.06
        "qty": 1000000,
        "turnover": 10050000.0,
        "upper_limit_price": 11.0,
        "lower_limit_price": 9.0,
        "open_price": 10.0,
        "high_price": 10.1,
        "low_price": 9.95,
        "pre_close_price": 10.0,
        "bid": [10.044, 10.03, 10.02, 10.01, 10.00],
        "ask": [10.056, 10.06, 10.07, 10.08, 10.09],
        "bid_qty": [100, 200, 300, 400, 500],
        "ask_qty": [150, 250, 350, 450, 550],
    }

    _on_depth_market_data(data, [], 0, 0, [], 0, 0, tick_queue, None)

    tick = tick_queue.get(timeout=2)
    assert tick.last_price == 10.06  # 四舍五入
    assert tick.name == "浦发银行"
    print("✓ 行情 pricetick 四舍五入正确")


def test_depth_market_data_unsupported_exchange():
    """测试不支持交易所的行情被忽略"""
    tick_queue = mp.Queue()

    data = {
        "ticker": "000001",
        "exchange_id": 99,  # 不支持的交易所
        "data_time": 20230703100000123000,
        "last_price": 10.0,
        "qty": 0,
        "turnover": 0.0,
        "upper_limit_price": 11.0,
        "lower_limit_price": 9.0,
        "open_price": 10.0,
        "high_price": 10.0,
        "low_price": 10.0,
        "pre_close_price": 10.0,
        "bid": [0] * 5,
        "ask": [0] * 5,
        "bid_qty": [0] * 5,
        "ask_qty": [0] * 5,
    }

    _on_depth_market_data(data, [], 0, 0, [], 0, 0, tick_queue, None)

    assert tick_queue.empty()
    print("✓ 不支持交易所的行情被正确忽略")


# ------------------------------------------------------------------
# 4. 合约查询处理测试
# ------------------------------------------------------------------

def test_query_all_tickers_conversion():
    """测试合约查询回报转换"""
    tick_queue = mp.Queue()

    data = {
        "ticker": "600000",
        "exchange_id": 1,
        "ticker_name": "浦发银行",
        "ticker_type": 0,
        "price_tick": 0.01,
        "buy_qty_unit": 100,
    }

    _on_query_all_tickers(data, {}, False, tick_queue)

    item = tick_queue.get(timeout=2)
    assert isinstance(item, tuple)
    assert item[0] == "contract"
    contract = item[1]
    assert isinstance(contract, ContractData)
    assert contract.symbol == "600000"
    assert contract.exchange == Exchange.SSE
    assert contract.name == "浦发银行"
    assert contract.pricetick == 0.01
    print("✓ 合约查询回报转换正确")


def test_query_all_tickers_empty_data():
    """测试空合约数据被忽略"""
    tick_queue = mp.Queue()
    _on_query_all_tickers({}, {}, False, tick_queue)
    assert tick_queue.empty()

    _on_query_all_tickers({"ticker": ""}, {}, False, tick_queue)
    assert tick_queue.empty()
    print("✓ 空合约数据被正确忽略")


# ------------------------------------------------------------------
# 5. 队列辅助函数测试
# ------------------------------------------------------------------

def test_drain_commands():
    """测试命令队列排空"""
    cmd_queue = mp.Queue()
    for i in range(5):
        cmd_queue.put({"action": "test", "idx": i})

    # 短暂等待确保所有 item 已入队（mp.Queue 在 spawn 模式下可能有延迟）
    time.sleep(0.05)
    commands = _drain_commands(cmd_queue)
    assert len(commands) == 5
    assert commands[0]["idx"] == 0
    assert commands[4]["idx"] == 4

    # 队列已空
    commands2 = _drain_commands(cmd_queue)
    assert len(commands2) == 0
    print("✓ 命令队列排空正确")


def test_normalize_queue_exception():
    """测试队列异常判断"""
    assert _normalize_queue_exception(queue.Empty())
    assert _normalize_queue_exception(EOFError())
    assert _normalize_queue_exception(OSError())
    assert _normalize_queue_exception(ValueError())
    assert not _normalize_queue_exception(RuntimeError())
    print("✓ 队列异常判断正确")


# ------------------------------------------------------------------
# 6. 网关默认配置测试
# ------------------------------------------------------------------

def test_default_setting():
    """测试默认配置"""
    setting = get_default_setting()
    assert setting["行情服务器"] == "119.3.103.38"
    assert setting["行情端口"] == 3002
    assert setting["通讯协议"] == "TCP"
    assert setting["日志级别"] == "INFO"
    assert setting["客户端ID"] == 1
    assert setting["心跳间隔"] == 15
    assert "本地网卡IP" in setting
    print("✓ 默认配置正确（含心跳间隔和本地网卡IP）")


# ------------------------------------------------------------------
# 7. 网关生命周期测试（mock EventEngine）
# ------------------------------------------------------------------

def test_gateway_lifecycle():
    """测试网关生命周期（不连接真实服务器）"""
    from vnpy.event.engine import EventEngine

    ee = EventEngine()
    gateway = XtpProGateway(ee, "XTP_PRO")

    assert gateway.default_name == "XTP_PRO"
    assert gateway._process_slots == []
    assert gateway._drain_thread is None
    assert not gateway._drain_active

    # send_order / cancel_order 占位
    assert gateway.send_order(MagicMock()) == ""
    gateway.cancel_order(MagicMock())  # 不应抛异常
    print("✓ 网关生命周期正确")


# ------------------------------------------------------------------
# 运行所有测试
# ------------------------------------------------------------------

def run_all_tests():
    """运行所有 mock 测试"""
    tests = [
        test_api_init,
        test_api_callbacks,
        test_api_disconnected_callback,
        test_api_subscribe_without_login,
        test_exchange_mapping,
        test_product_mapping,
        test_protocol_loglevel_mapping,
        test_depth_market_data_conversion,
        test_depth_market_data_with_pricetick,
        test_depth_market_data_unsupported_exchange,
        test_query_all_tickers_conversion,
        test_query_all_tickers_empty_data,
        test_drain_commands,
        test_normalize_queue_exception,
        test_default_setting,
        test_gateway_lifecycle,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} 失败: {e}")
            failed += 1

    print(f"\n{'='*40}")
    print(f"通过: {passed}/{len(tests)}")
    if failed:
        print(f"失败: {failed}")
    else:
        print("所有 mock 测试通过 ✓")


if __name__ == "__main__":
    run_all_tests()
