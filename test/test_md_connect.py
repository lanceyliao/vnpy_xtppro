"""
XTP Pro 行情网关测试

使用公网测试环境验证 MD 连接和行情订阅。
需要先安装 xtp_pro_api_python 和 vnpy。

用法:
    python -m test.test_md_connect
"""

import time
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vnpy.trader.object import SubscribeRequest
from vnpy.trader.constant import Exchange
from vnpy_xtppro import XtpProGateway


# 测试参数
TEST_SETTING = {
    "用户名": "453191001783",
    "密码": "",  # 需要填入密码
    "客户端ID": 1,
    "行情服务器": "122.112.252.150",
    "行情端口": 3002,
    "通讯协议": "TCP",
    "日志级别": "INFO",
    "配置文件": "",
}

# 测试订阅合约
TEST_SYMBOLS = [
    ("600000", Exchange.SSE),   # 浦发银行
    ("000001", Exchange.SZSE),  # 平安银行
    ("510050", Exchange.SSE),   # 50ETF
]


def test_md_connect():
    """测试行情连接"""
    from vnpy.event.engine import EventEngine

    # 创建事件引擎
    ee = EventEngine()
    ee.start()

    # 注册事件监听
    from vnpy.trader.event import EVENT_TICK, EVENT_CONTRACT, EVENT_LOG

    tick_count = 0
    contract_count = 0

    def on_tick(event):
        nonlocal tick_count
        tick = event.data
        tick_count += 1
        if tick_count <= 5:
            print(f"[TICK] {tick.symbol} {tick.last_price} vol={tick.volume} @ {tick.datetime}")
        elif tick_count == 6:
            print(f"[TICK] ... (后续行情省略)")

    def on_contract(event):
        nonlocal contract_count
        contract = event.data
        contract_count += 1
        if contract_count <= 3:
            print(f"[CONTRACT] {contract.symbol} {contract.name} {contract.exchange}")

    def on_log(event):
        log = event.data
        print(f"[LOG] {log.msg}")

    ee.register(EVENT_TICK, on_tick)
    ee.register(EVENT_CONTRACT, on_contract)
    ee.register(EVENT_LOG, on_log)

    # 创建网关
    gateway = XtpProGateway(ee, "XTP_PRO")
    gateway.connect(TEST_SETTING)

    print("等待合约查询...")
    time.sleep(10)

    print(f"已收到 {contract_count} 个合约")

    # 订阅行情
    for symbol, exchange in TEST_SYMBOLS:
        req = SubscribeRequest(symbol=symbol, exchange=exchange)
        gateway.subscribe(req)
        print(f"已订阅: {symbol} {exchange.value}")

    print("等待行情推送...")
    time.sleep(30)

    print(f"\n=== 测试结果 ===")
    print(f"收到合约: {contract_count}")
    print(f"收到行情: {tick_count}")

    # 关闭
    gateway.close()
    ee.stop()

    print("测试完成")


if __name__ == "__main__":
    test_md_connect()
