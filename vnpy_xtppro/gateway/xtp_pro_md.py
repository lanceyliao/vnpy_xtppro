"""
XTP Pro 独立进程行情网关

架构设计（参考 vnpy_tq 多进程模式）:
- 主进程: XtpProGateway，接收事件、驱动策略
- MD worker 进程: 独立进程中运行 vnxtpxquote.QuoteApi
  - 通过 command_queue 接收订阅指令
  - 通过 tick_queue / log_queue 回传行情数据
  - 通过 stop_event 控制生命周期
- 主进程 drain 线程: 从 tick_queue 消费 TickData，推入 EventEngine

优势:
- XTP Pro C++ SDK 的回调在子进程中处理，不阻塞主进程 GIL
- 全量 event_tick 在子进程聚合后，只推 event_bar 到主进程（降低主进程负载）
- 子进程崩溃不影响主进程，可自动重启
"""

import multiprocessing as mp
import queue
import threading
import time
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from vnpy.trader.constant import Exchange, Product
from vnpy.trader.object import ContractData, SubscribeRequest, TickData
from vnpy.trader.utility import get_folder_path, round_to, ZoneInfo

from ..api.xtp_pro_md_api import XtpProMdApi
from .bar_generator import BarGenerator


# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# 交易所映射（行情 exchange_id）
EXCHANGE_XTP2VT: dict[int, Exchange] = {
    1: Exchange.SSE,    # 上证
    2: Exchange.SZSE,   # 深证
}
EXCHANGE_VT2XTP: dict[Exchange, int] = {v: k for k, v in EXCHANGE_XTP2VT.items()}

# 产品类型映射
PRODUCT_XTP2VT: dict[int, Product] = {
    0: Product.EQUITY,
    1: Product.INDEX,
    2: Product.FUND,
    3: Product.BOND,
    4: Product.OPTION,
    5: Product.EQUITY,
    6: Product.FUND,
}

# 通讯协议
PROTOCOL_VT2XTP: dict[str, int] = {
    "TCP": 1,
    "UDP": 2,
}

# 日志级别
LOGLEVEL_VT2XTP: dict[str, int] = {
    "FATAL": 0,
    "ERROR": 1,
    "WARNING": 2,
    "INFO": 3,
    "DEBUG": 4,
    "TRACE": 5,
}

# 队列参数
QUEUE_DRAIN_BATCH_SIZE = 2000
QUEUE_DRAIN_IDLE_SLEEP = 0.01

# 进程命令
CMD_SUBSCRIBE = "subscribe"
CMD_UNSUBSCRIBE = "unsubscribe"
CMD_STOP = "stop"

# 合约数据全局缓存
symbol_contract_map: dict[str, ContractData] = {}


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _normalize_queue_exception(exc: BaseException) -> bool:
    """判断队列异常是否为正常的空/关闭"""
    return isinstance(exc, (queue.Empty, EOFError, OSError, ValueError))


def _drain_commands(command_queue: mp.Queue) -> List[dict]:
    """一次性排空命令队列"""
    commands: List[dict] = []
    while True:
        try:
            commands.append(command_queue.get_nowait())
        except Exception as exc:
            if _normalize_queue_exception(exc):
                return commands
            raise


# ------------------------------------------------------------------
# 子进程 worker
# ------------------------------------------------------------------

def _md_process_worker(
    userid: str,
    password: str,
    client_id: int,
    server_ip: str,
    server_port: int,
    protocol: int,
    log_level: int,
    config_file: str,
    tick_queue: mp.Queue,
    bar_queue: mp.Queue,
    log_queue: mp.Queue,
    command_queue: mp.Queue,
    stop_event: mp.Event,
) -> None:
    """
    行情子进程 worker

    在独立进程中运行 XtpProMdApi，接收订阅命令，推送行情数据。
    tick → BarGenerator → bar_queue（分钟线）
    tick_queue 仅用于合约数据传递。
    """
    api: Optional[XtpProMdApi] = None
    subscribed_symbols: Set[str] = set()

    # Bar 合成器：tick → 分钟 bar
    bar_gen = BarGenerator(
        on_bar=lambda bar: bar_queue.put(bar),
        on_bar_gap=lambda bar: bar_queue.put(("bar_gap", bar)),
        write_log=lambda msg: log_queue.put(msg),
    )

    try:
        api = XtpProMdApi()

        # 设置回调
        api.on_disconnected = lambda reason: (
            log_queue.put(f"行情连接断开, 原因: {reason}"),
            bar_gen.force_finish_all(),
        )
        api.on_error = lambda error: log_queue.put(f"行情错误: {error}")
        api.on_sub_market_data = lambda data, error, last: None
        api.on_depth_market_data = lambda data, bql, bc, mbc, aql, ac, mac: _on_depth_market_data(
            data, bql, bc, mbc, aql, ac, mac, tick_queue, bar_gen
        )
        api.on_query_all_tickers = lambda data, error, last: _on_query_all_tickers(
            data, error, last, tick_queue
        )
        api.on_tick_by_tick = lambda data: None  # 可扩展

        # 连接
        api.connect(
            userid, password, client_id,
            server_ip, server_port,
            protocol, log_level, config_file,
        )
        log_queue.put(f"XTP Pro 行情子进程已连接 {server_ip}:{server_port}")

        # 查询合约
        for exchange_id in EXCHANGE_XTP2VT:
            api.query_all_tickers(exchange_id)
        log_queue.put("已发起合约查询")

        # 启动 API 回调线程
        api.init()

        # 主循环：处理命令
        while not stop_event.is_set():
            for command in _drain_commands(command_queue):
                action = command.get("action")
                if action == CMD_STOP:
                    return
                elif action == CMD_SUBSCRIBE:
                    symbol = command["symbol"]
                    exchange_id = command["exchange_id"]
                    if symbol not in subscribed_symbols:
                        api.subscribe_market_data(symbol, exchange_id)
                        subscribed_symbols.add(symbol)
                elif action == CMD_UNSUBSCRIBE:
                    symbol = command["symbol"]
                    exchange_id = command["exchange_id"]
                    if symbol in subscribed_symbols:
                        api.unsubscribe_market_data(symbol, exchange_id)
                        subscribed_symbols.discard(symbol)

            time.sleep(0.01)  # 避免 CPU 空转

    except Exception as e:
        log_queue.put(f"行情子进程异常: {e}")
    finally:
        if api:
            try:
                api.close()
            except Exception:
                pass
        log_queue.put("行情子进程已终止")


def _on_depth_market_data(
    data: dict,
    bid1_qty_list: list,
    bid1_count: int,
    max_bid1_count: int,
    ask1_qty_list: list,
    ask1_count: int,
    max_ask1_count: int,
    tick_queue: mp.Queue,
    bar_gen: BarGenerator,
) -> None:
    """处理深度行情推送，转换为 TickData 放入队列，并合成 bar"""
    try:
        timestamp: str = str(data["data_time"])
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d%H%M%S%f")
        dt = dt.replace(tzinfo=CHINA_TZ)

        exchange = EXCHANGE_XTP2VT.get(data["exchange_id"])
        if exchange is None:
            return

        tick: TickData = TickData(
            symbol=data["ticker"],
            exchange=exchange,
            datetime=dt,
            volume=data["qty"],
            turnover=data["turnover"],
            last_price=data["last_price"],
            limit_up=data["upper_limit_price"],
            limit_down=data["lower_limit_price"],
            open_price=data["open_price"],
            high_price=data["high_price"],
            low_price=data["low_price"],
            pre_close=data["pre_close_price"],
            gateway_name="XTP_PRO",
        )

        # 五档买卖盘
        tick.bid_price_1, tick.bid_price_2, tick.bid_price_3, tick.bid_price_4, tick.bid_price_5 = data["bid"][0:5]
        tick.ask_price_1, tick.ask_price_2, tick.ask_price_3, tick.ask_price_4, tick.ask_price_5 = data["ask"][0:5]
        tick.bid_volume_1, tick.bid_volume_2, tick.bid_volume_3, tick.bid_volume_4, tick.bid_volume_5 = data["bid_qty"][0:5]
        tick.ask_volume_1, tick.ask_volume_2, tick.ask_volume_3, tick.ask_volume_4, tick.ask_volume_5 = data["ask_qty"][0:5]

        # 基于合约最小价格跳动四舍五入
        contract: ContractData = symbol_contract_map.get(tick.vt_symbol, None)
        if contract:
            pricetick: float = contract.pricetick
            tick.last_price = round_to(data["last_price"], pricetick)
            tick.limit_up = round_to(data["upper_limit_price"], pricetick)
            tick.limit_down = round_to(data["lower_limit_price"], pricetick)
            tick.open_price = round_to(data["open_price"], pricetick)
            tick.high_price = round_to(data["high_price"], pricetick)
            tick.low_price = round_to(data["low_price"], pricetick)
            tick.pre_close = round_to(data["pre_close_price"], pricetick)

            for i in range(5):
                setattr(tick, f"bid_price_{i+1}", round_to(data["bid"][i], pricetick))
                setattr(tick, f"ask_price_{i+1}", round_to(data["ask"][i], pricetick))

            tick.name = contract.name

        tick_queue.put(tick)

        # tick → bar 合成（在子进程中完成）
        if bar_gen is not None:
            bar_gen.update_tick(tick)
    except Exception:
        pass  # 行情处理不应阻塞


def _on_query_all_tickers(
    data: dict,
    error: dict,
    last: bool,
    tick_queue: mp.Queue,
) -> None:
    """处理合约查询回报，转换为 ContractData 放入队列"""
    try:
        if not data or not data.get("ticker"):
            return

        exchange = EXCHANGE_XTP2VT.get(data["exchange_id"])
        if exchange is None:
            return

        contract: ContractData = ContractData(
            symbol=data["ticker"],
            exchange=exchange,
            name=data.get("ticker_name", ""),
            product=PRODUCT_XTP2VT.get(data.get("ticker_type", 0), Product.EQUITY),
            size=1,
            pricetick=data.get("price_tick", 0.01),
            min_volume=data.get("buy_qty_unit", 1),
            gateway_name="XTP_PRO",
        )

        # 通过特殊标记传递合约数据
        tick_queue.put(("contract", contract, last))
    except Exception:
        pass
