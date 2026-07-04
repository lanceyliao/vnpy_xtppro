"""VeighNa + XTP Pro 行情网关 — 无 UI 启动脚本

用法:
    python script/run.py

功能:
    - 创建 MainEngine + EventEngine
    - 添加 XTP_PRO 网关
    - 连接行情服务器
    - 订阅合约
    - 监听 tick / bar 事件并打印
"""

from time import sleep

from vnpy.event import EventEngine, Event
from vnpy.trader.engine import MainEngine
from vnpy.trader.object import SubscribeRequest, TickData, BarData
from vnpy.trader.constant import Exchange

from vnpy_xtppro import XtpProGateway
from vnpy_xtppro.gateway import EVENT_BAR


# ── 网关配置 ──────────────────────────────────────────────────────────
xtp_pro_setting = {
    "用户名": "",
    "密码": "",
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

# ── 要订阅的合约列表 ─────────────────────────────────────────────────
SUBSCRIBES = [
    ("600000", Exchange.SSE),   # 浦发银行
    ("000001", Exchange.SZSE),  # 平安银行
]


# ── 事件回调 ─────────────────────────────────────────────────────────
def on_tick(event: Event) -> None:
    """通用 tick 回调 — 收到所有合约的 tick"""
    tick: TickData = event.data
    print(f"TICK {tick.vt_symbol} {tick.datetime} "
          f"last={tick.last_price} vol={tick.volume}")


def on_bar(event: Event) -> None:
    """通用 bar 回调 — 收到所有合约的 bar"""
    bar: BarData = event.data
    print(f"BAR  {bar.vt_symbol} {bar.datetime} "
          f"C={bar.close_price} V={bar.volume}"
          f"{' [gap]' if bar.volume == 0 else ''}")


def on_bar_600000(event: Event) -> None:
    """特定合约 bar 回调 — 只收 600000.SSE"""
    bar: BarData = event.data
    print(f"  → 600000 专用: {bar.datetime} C={bar.close_price}")


# ── 主流程 ───────────────────────────────────────────────────────────
def main() -> None:
    # 1. 创建引擎
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    # 2. 添加网关
    main_engine.add_gateway(XtpProGateway)

    # 3. 注册事件回调
    #    - EVENT_TICK:        收所有合约的 tick
    #    - EVENT_TICK + sym:  只收特定合约的 tick（vnpy 核心自动推送）
    event_engine.register(EVENT_TICK := "eTick.", on_tick)

    #    - EVENT_BAR:        收所有合约的 bar
    #    - EVENT_BAR + sym:  只收特定合约的 bar
    event_engine.register(EVENT_BAR, on_bar)
    event_engine.register(EVENT_BAR + "600000.SSE", on_bar_600000)

    # 4. 连接（等待合约查询完成后返回）
    main_engine.connect(xtp_pro_setting, "XTP_PRO")
    print("XTP Pro 连接成功，合约已加载")

    # 5. 订阅行情
    for symbol, exchange in SUBSCRIBES:
        req = SubscribeRequest(symbol=symbol, exchange=exchange)
        main_engine.subscribe(req, "XTP_PRO")
    print(f"已订阅 {len(SUBSCRIBES)} 个合约")

    # 6. 启动事件引擎
    event_engine.start()
    print("事件引擎已启动，按 Ctrl+C 退出")

    # 7. 主循环
    try:
        while True:
            sleep(1)
    except KeyboardInterrupt:
        print("\n正在关闭...")
        main_engine.close()
        print("已关闭")


if __name__ == "__main__":
    main()
