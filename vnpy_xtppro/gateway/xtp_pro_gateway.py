"""
XTP Pro 行情网关

VeighNa BaseGateway 适配层，驱动独立进程 MD worker，
通过 multiprocessing.Queue 消费行情数据并推入 EventEngine。

包含：
- BarGenerator: tick→分钟线合成 + 缺失bar补充
- XtpProGateway: 行情网关主类
- 子进程 worker + 行情数据处理函数
"""

import multiprocessing as mp
import queue
import threading
import time
import traceback
from copy import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from vnpy.trader.constant import Exchange, Interval, Product
from vnpy.trader.event import EVENT_CONTRACT, EVENT_LOG, EVENT_TICK
from vnpy.event.engine import Event
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    BarData,
    CancelRequest,
    ContractData,
    LogData,
    OrderRequest,
    SubscribeRequest,
    TickData,
)
from vnpy.trader.utility import get_folder_path, round_to, ZoneInfo

from ..api.xtp_pro_md_api import XtpProMdApi

# ------------------------------------------------------------------
# 常量
# ------------------------------------------------------------------

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# 交易所映射（行情 exchange_id）
EXCHANGE_XTP2VT: dict[int, Exchange] = {
    1: Exchange.SSE,    # 上证
    2: Exchange.SZSE,   # 深证
    3: Exchange.BSE,    # 北交所 (XTP_EXCHANGE_NQ)
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

# XTP Pro data_type_v2 枚举（XTPMD.data_type_v2 字段值）
# 用于区分快照行情的 union 子字典类型 (stk/opt/bond)
DATA_TYPE_V2_INDEX: int  = 1   # 指数
DATA_TYPE_V2_OPTION: int = 2   # 期权
DATA_TYPE_V2_ACTUAL: int = 3   # 现货（股票/基金/可转债等）
DATA_TYPE_V2_BOND: int   = 4   # 债券（国债逆回购等）

DATA_TYPE_V2_MAP: dict[int, str] = {
    DATA_TYPE_V2_INDEX:  "INDEX",
    DATA_TYPE_V2_OPTION: "OPTION",
    DATA_TYPE_V2_ACTUAL: "ACTUAL",
    DATA_TYPE_V2_BOND:   "BOND",
}

# 公网测试环境订阅限制（实盘无此限制）
# 单订阅: 每个市场最多 100 只，沪深合计 200 只
# 全订阅: 沪深合计仅推送 7 只合约
# 实盘 UDP 无数量限制，但务必先做接入测试
MAX_SUBSCRIBE_PER_MARKET_TEST: int = 100

# 多进程分片：每个 worker 进程的订阅容量，超限自动起新进程
MAX_QUOTES_PER_PROCESS: int = 500

# 队列参数
QUEUE_DRAIN_BATCH_SIZE = 2000
QUEUE_DRAIN_IDLE_SLEEP = 0.01

# 进程命令
CMD_SUBSCRIBE = "subscribe"
CMD_UNSUBSCRIBE = "unsubscribe"
CMD_STOP = "stop"

# 合约数据全局缓存
symbol_contract_map: dict[str, ContractData] = {}

# A股交易时段 (start_hour, start_min, end_hour, end_min)
TRADING_SESSIONS = [
    (9, 30, 11, 30),   # 早盘
    (13, 0, 15, 0),    # 午盘
]

# 自定义事件类型（VeighNa 核心未内置 EVENT_BAR）
EVENT_BAR = "eBar."

# ------------------------------------------------------------------
# 交易时段辅助函数
# ------------------------------------------------------------------

def same_trading_session(dt1: datetime, dt2: datetime) -> bool:
    """判断两个时间是否在同一交易时段内（同日 + 同时段）"""
    if dt1.date() != dt2.date():
        return False
    t1 = dt1.hour * 60 + dt1.minute
    t2 = dt2.hour * 60 + dt2.minute
    for start_h, start_m, end_h, end_m in TRADING_SESSIONS:
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if start <= t1 <= end and start <= t2 <= end:
            return True
    return False

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
# Bar 合成器
# ------------------------------------------------------------------

DB_TZ = ZoneInfo("Asia/Shanghai")


class BarGenerator:
    """
    分钟线合成器

    架构：
    - update_tick: 每个 tick 到来时更新当前 bar
    - 当分钟切换时，推送上一分钟完成的 bar
    - 检测缺失 bar（断连导致），自动补充零量 bar
    - 仅在同一交易时段内补充缺失 bar，跨时段不补充
    - 通过 on_bar 回调推送合成后的 bar（正常 bar 和补充 bar 统一走 on_bar）
    """

    def __init__(
        self,
        on_bar: Callable[[BarData], None],
        write_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            on_bar: bar 推送回调（正常 + 补充的 bar 都会调用）
            write_log: 日志回调（可选）
        """
        self.on_bar: Callable[[BarData], None] = on_bar
        self.write_log: Callable[[str], None] = write_log or (lambda msg: None)

        # 当前正在构建的 bar（按 vt_symbol 索引）
        self.bars: Dict[str, Optional[BarData]] = {}

        # 上一个 tick（用于计算增量成交额）
        self.last_ticks: Dict[str, TickData] = {}

        # 上一个 bar 的分钟 datetime（用于检测缺失）
        self.last_dts: Dict[str, datetime] = {}

        # 上一个完成的 bar（用于缺失补充）
        self.last_bars: Dict[str, BarData] = {}

    def update_tick(self, tick: TickData) -> None:
        """基于 tick 合成分钟线

        逻辑：
        1. 检测分钟切换 → 推送上一分钟 bar
        2. 创建或更新当前分钟 bar
        3. 累加增量成交量和成交额
        """
        vt_symbol: str = tick.vt_symbol

        # ---- 分钟切换检测 ----
        last_dt: Optional[datetime] = self.last_dts.get(vt_symbol)
        if not last_dt or last_dt.minute != tick.datetime.minute:
            # 分钟已切换，推送上一分钟的 bar
            bar: Optional[BarData] = self.bars.get(vt_symbol)
            if bar:
                bar.datetime = bar.datetime.replace(second=0, microsecond=0)
                self.process_bar(bar)
                self.bars[vt_symbol] = None

        # ---- 创建或更新当前 bar ----
        bar = self.bars.get(vt_symbol)
        if not bar:
            bar = BarData(
                symbol=tick.symbol,
                exchange=tick.exchange,
                interval=Interval.MINUTE,
                datetime=tick.datetime.replace(second=0, microsecond=0),
                gateway_name=tick.gateway_name,
                open_price=tick.last_price,
                high_price=tick.last_price,
                low_price=tick.last_price,
                close_price=tick.last_price,
                open_interest=tick.open_interest,
            )
            self.bars[vt_symbol] = bar
        else:
            bar.high_price = max(bar.high_price, tick.last_price)
            bar.low_price = min(bar.low_price, tick.last_price)
            bar.close_price = tick.last_price
            bar.open_interest = tick.open_interest

        # ---- 累加增量成交量/额 ----
        bar.volume += tick.last_volume
        last_tick: Optional[TickData] = self.last_ticks.get(vt_symbol)
        if last_tick:
            bar.turnover += max(tick.turnover - last_tick.turnover, 0)

        self.last_ticks[vt_symbol] = tick
        self.last_dts[vt_symbol] = tick.datetime

    def process_bar(self, bar: BarData) -> None:
        """处理完成的 bar，包括记录和补充缺失

        缺失补充逻辑：
        - 仅在同一交易时段内补充（same_trading_session）
        - 跨时段（早盘→午盘、昨日→今日）不补充
        - 首根 bar（无 last_bar）不补充
        - 缺失 bar 用 last_bar 的 close_price 构造零量 bar
        """
        try:
            last_bar: Optional[BarData] = self.last_bars.get(bar.vt_symbol)

            if last_bar:
                self.write_log(
                    f"process_bar last:{last_bar.datetime} now:{bar.datetime}"
                )

                # 统一时区
                if last_bar.datetime.tzinfo is None:
                    last_bar_datetime = last_bar.datetime.replace(tzinfo=DB_TZ)
                else:
                    last_bar_datetime = last_bar.datetime

                if bar.datetime.tzinfo is None:
                    bar_datetime = bar.datetime.replace(tzinfo=DB_TZ)
                else:
                    bar_datetime = bar.datetime

                minutes_diff: int = int(
                    (bar_datetime - last_bar_datetime).total_seconds() / 60
                )

                if minutes_diff > 1 and same_trading_session(last_bar_datetime, bar_datetime):
                    # 仅在同一交易时段内补充缺失 bar
                    for i in range(1, minutes_diff):
                        gap_dt = last_bar_datetime + timedelta(minutes=i)
                        gap_bar = BarData(
                            symbol=bar.symbol,
                            exchange=bar.exchange,
                            interval=Interval.MINUTE,
                            datetime=gap_dt,
                            gateway_name=bar.gateway_name,
                            open_price=last_bar.close_price,
                            high_price=last_bar.close_price,
                            low_price=last_bar.close_price,
                            close_price=last_bar.close_price,
                            volume=0,
                            turnover=0,
                            open_interest=last_bar.open_interest,
                        )
                        self.write_log(
                            f"补充缺失bar: {gap_bar.vt_symbol}, "
                            f"时间: {gap_bar.datetime}, "
                            f"价格: {gap_bar.close_price}"
                        )
                        # 统一走 on_bar（不再区分 on_bar_gap）
                        self.on_bar(gap_bar)

            # 记录当前 bar
            self.on_bar(bar)
            self.last_bars[bar.vt_symbol] = bar

        except Exception:
            msg = f"process_bar触发异常已停止\n{traceback.format_exc()}"
            self.write_log(msg)

    def force_finish_all(self) -> None:
        """强制完成所有未推送的 bar（连接断开时调用）"""
        for vt_symbol, bar in list(self.bars.items()):
            if bar:
                bar.datetime = bar.datetime.replace(second=0, microsecond=0)
                self.on_bar(bar)
                self.last_bars[vt_symbol] = bar
                self.bars[vt_symbol] = None

# ------------------------------------------------------------------
# 子进程行情数据处理
# ------------------------------------------------------------------

# 子进程内缓存：上一 tick 累计成交量（用于计算 last_volume 增量）
_symbol_last_cum_volume: Dict[str, float] = {}


def _on_depth_market_data(
    data: dict,
    bid1_qty_list: list,
    bid1_count: int,
    max_bid1_count: int,
    ask1_qty_list: list,
    ask1_count: int,
    max_ask1_count: int,
    tick_queue: mp.Queue,
    bar_gen: Optional[BarGenerator],
) -> None:
    """处理深度行情推送，转换为 TickData 放入队列，并合成 bar"""
    try:
        timestamp: str = str(data["data_time"])
        dt: datetime = datetime.strptime(timestamp, "%Y%m%d%H%M%S%f")
        dt = dt.replace(tzinfo=CHINA_TZ)

        exchange = EXCHANGE_XTP2VT.get(data["exchange_id"])
        if exchange is None:
            return

        # XTP Pro: data_type_v2 区分 stk/opt/bond 子字典
        # data_type 已移除，必须使用 data_type_v2
        data_type_v2: int = data.get("data_type_v2", 0)

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

        # XTP Pro 透传交易所原始 ticker_status（不再做转换）
        # 沪市: ticker_status[0]=S/C/T/E/P/M/N/U, [1]=0/1, [2]=0/1, [3]=0/1
        # 深市: ticker_status[0]=S/O/T/B/C/E/H/A/V, [1]=0/1
        ticker_status: str = data.get("ticker_status", "")
        if ticker_status:
            tick.extra = {"ticker_status": ticker_status, "data_type_v2": data_type_v2}

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

        # 计算 last_volume（增量成交量 = 当前累计 - 上次累计）
        cum_volume: float = data["qty"]
        last_cum: float = _symbol_last_cum_volume.get(tick.vt_symbol, 0)
        tick.last_volume = max(cum_volume - last_cum, 0)
        _symbol_last_cum_volume[tick.vt_symbol] = cum_volume

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


# ------------------------------------------------------------------
# 子进程 worker
# ------------------------------------------------------------------

def _md_process_worker(
    process_id: int,
    userid: str,
    password: str,
    client_id: int,
    server_ip: str,
    server_port: int,
    protocol: int,
    log_level: int,
    config_file: str,
    heartbeat_interval: int,
    local_ip: str,
    max_quotes: int,
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

    # Bar 合成器：tick → 分钟 bar（统一 on_bar，无 on_bar_gap）
    bar_gen = BarGenerator(
        on_bar=lambda bar: bar_queue.put(bar),
        write_log=lambda msg: log_queue.put(msg),
    )

    # 合约查询同步：等待 is_last 后再查询下一个市场
    query_complete_event: threading.Event = threading.Event()

    def _on_query_all_tickers_impl(
        data: dict,
        error: dict,
        last: bool,
    ) -> None:
        """处理合约查询回报，转换为 ContractData 放入队列"""
        _on_query_all_tickers(data, error, last, tick_queue)
        if last:
            query_complete_event.set()

    try:
        api = XtpProMdApi()

        # 重连控制
        disconnected: bool = False
        # FAQ 2.1.21: 重连等待必须 >= 心跳间隔，否则登录不成功
        reconnect_delay: float = max(5.0, float(heartbeat_interval))

        # 设置回调
        def _on_disconnected(reason: int) -> None:
            nonlocal disconnected
            disconnected = True
            log_queue.put(f"行情连接断开, 原因: {reason}, 将在 {reconnect_delay}s 后重连")
            bar_gen.force_finish_all()

        api.on_disconnected = _on_disconnected
        api.on_error = lambda error: log_queue.put(f"行情错误: {error}")
        api.on_sub_market_data = lambda data, error, last: None
        api.on_depth_market_data = lambda data, bql, bc, mbc, aql, ac, mac: _on_depth_market_data(
            data, bql, bc, mbc, aql, ac, mac, tick_queue, bar_gen
        )
        api.on_query_all_tickers = _on_query_all_tickers_impl
        api.on_tick_by_tick = lambda data: None  # 可扩展

        # 连接
        api.connect(
            userid, password, client_id,
            server_ip, server_port,
            protocol, log_level, config_file,
            heartbeat_interval, local_ip,
        )
        log_queue.put(f"XTP Pro 行情子进程已连接 {server_ip}:{server_port}")

        # FAQ 2.1.23: 实盘 UDP 必须配置 config_file，否则无法获取行情
        if protocol == 2 and not config_file:
            log_queue.put("警告: UDP模式未设置配置文件(quote_config.ini)，实盘环境将无法获取行情！")

        # 查询合约：逐市场查询，等待 is_last 后再查下一个（否则会断线！）
        for exchange_id in EXCHANGE_XTP2VT:
            query_complete_event.clear()
            api.query_all_tickers(exchange_id)
            query_complete_event.wait(timeout=30)  # 最多等30秒
        log_queue.put("已发起合约查询")

        # 启动 API 回调线程
        api.init()

        # 主循环：处理命令 + 重连
        while not stop_event.is_set():
            # ---- 重连逻辑 ----
            if disconnected:
                time.sleep(reconnect_delay)
                if stop_event.is_set():
                    break
                try:
                    api.close()
                except Exception:
                    pass

                log_queue.put(f"正在重连 {server_ip}:{server_port} ...")
                try:
                    api = XtpProMdApi()
                    api.on_disconnected = _on_disconnected
                    api.on_error = lambda error: log_queue.put(f"行情错误: {error}")
                    api.on_sub_market_data = lambda data, error, last: None
                    api.on_depth_market_data = lambda data, bql, bc, mbc, aql, ac, mac: _on_depth_market_data(
                        data, bql, bc, mbc, aql, ac, mac, tick_queue, bar_gen
                    )
                    api.on_query_all_tickers = _on_query_all_tickers_impl
                    api.on_tick_by_tick = lambda data: None

                    api.connect(
                        userid, password, client_id,
                        server_ip, server_port,
                        protocol, log_level, config_file,
                        heartbeat_interval, local_ip,
                    )
                    api.init()
                    disconnected = False
                    log_queue.put(f"重连成功 {server_ip}:{server_port}")

                    # 重新查询合约（逐市场，等待 is_last）
                    for exchange_id in EXCHANGE_XTP2VT:
                        query_complete_event.clear()
                        api.query_all_tickers(exchange_id)
                        query_complete_event.wait(timeout=30)
                    log_queue.put("重连后已发起合约查询")

                    # 重新订阅：TCP需要重新订阅，UDP组播不受影响无需重新订阅
                    if protocol == 1:  # TCP
                        for sym in list(subscribed_symbols):
                            parts = sym.split(".")
                            if len(parts) == 2:
                                api.subscribe_market_data(parts[0], int(parts[1]))
                        if subscribed_symbols:
                            log_queue.put(f"重连后已重新订阅 {len(subscribed_symbols)} 个合约")
                    else:
                        log_queue.put("UDP模式重连，组播行情不受影响，无需重新订阅")

                except Exception as e:
                    log_queue.put(f"重连失败: {e}, 将在 {reconnect_delay}s 后重试")

            # ---- 处理命令 ----
            for command in _drain_commands(command_queue):
                action = command.get("action")
                if action == CMD_STOP:
                    return
                elif action == CMD_SUBSCRIBE:
                    symbol = command["symbol"]
                    exchange_id = command["exchange_id"]
                    if len(subscribed_symbols) >= max_quotes:
                        log_queue.put(f"进程 #{process_id} 订阅已达上限 {max_quotes}，跳过 {symbol}")
                        continue
                    if symbol not in subscribed_symbols:
                        # 公网测试环境提醒
                        import datetime as _dt
                        now = _dt.datetime.now()
                        if now.hour < 8 or (now.hour == 8 and now.minute < 50):
                            log_queue.put("提示: 8:50前订阅可能失败(11200404)，交易所尚未推送快照")
                        if exchange_id == 3:  # XTP_EXCHANGE_NQ (北交所)
                            log_queue.put("提示: 公网测试环境无北交所行情，实盘UDP正常")
                        api.subscribe_market_data(symbol, exchange_id)
                        subscribed_symbols.add(f"{symbol}.{exchange_id}")
                elif action == CMD_UNSUBSCRIBE:
                    symbol = command["symbol"]
                    exchange_id = command["exchange_id"]
                    key = f"{symbol}.{exchange_id}"
                    if key in subscribed_symbols:
                        api.unsubscribe_market_data(symbol, exchange_id)
                        subscribed_symbols.discard(key)

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


# ------------------------------------------------------------------
# 网关主类
# ------------------------------------------------------------------

DEFAULT_SERVER = "119.3.103.38"
DEFAULT_PORT = 3002
DEFAULT_PROTOCOL = "TCP"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_HEARTBEAT_INTERVAL = 15
DEFAULT_MAX_QUOTES_PER_PROCESS = 500  # 每进程订阅容量，超限自动起新进程


class XtpProGateway(BaseGateway):
    """
    XTP Pro 行情网关

    使用独立进程运行 XTP Pro 行情 API，
    通过 multiprocessing.Queue 将行情数据传回主进程。
    """

    default_name: str = "XTP_PRO"

    def __init__(self, event_engine, gateway_name: str = "XTP_PRO") -> None:
        super().__init__(event_engine, gateway_name)

        # 共享数据队列（所有子进程写入同一队列）
        self._tick_queue: mp.Queue = mp.Queue()
        self._bar_queue: mp.Queue = mp.Queue()
        self._log_queue: mp.Queue = mp.Queue()
        self._stop_event: mp.Event = mp.Event()

        # 多进程分片：每个 slot 管理一个子进程
        self._process_slots: List[Dict[str, Any]] = []

        # 主进程 drain 线程
        self._drain_thread: Optional[threading.Thread] = None
        self._drain_active: bool = False

        # 连接参数
        self._userid: str = ""
        self._password: str = ""
        self._client_id: int = 0
        self._server_ip: str = ""
        self._server_port: int = 0
        self._protocol: int = 1
        self._log_level: int = 3
        self._config_file: str = ""

        # 订阅管理
        self._subscribed: Set[str] = set()
        self._max_quotes: int = DEFAULT_MAX_QUOTES_PER_PROCESS

        # 合约查询同步（connect 等待合约批量推送完毕再返回）
        self._contracts_ready: threading.Event = threading.Event()
        self._exchanges_queried: int = 0

    # ------------------------------------------------------------------
    # BaseGateway 接口
    # ------------------------------------------------------------------

    def connect(self, setting: dict) -> None:
        """连接行情服务器

        启动第一个 worker 进程，等待合约查询完成后返回。
        这样 on_contract 在 subscribe 之前批量触发完毕，
        与 vnpy_tq / vnpy_ctp 行为一致。
        """
        self._userid = setting.get("用户名", "")
        self._password = setting.get("密码", "")
        self._client_id = int(setting.get("客户端ID", 1))
        self._server_ip = setting.get("行情服务器", DEFAULT_SERVER)
        self._server_port = int(setting.get("行情端口", DEFAULT_PORT))
        self._protocol = PROTOCOL_VT2XTP.get(setting.get("通讯协议", DEFAULT_PROTOCOL), 1)
        self._log_level = LOGLEVEL_VT2XTP.get(setting.get("日志级别", DEFAULT_LOG_LEVEL), 3)
        self._config_file = setting.get("配置文件", "")
        self._heartbeat_interval = int(setting.get("心跳间隔", DEFAULT_HEARTBEAT_INTERVAL))
        self._local_ip = setting.get("本地网卡IP", "")
        self._max_quotes = int(setting.get("每进程订阅数", DEFAULT_MAX_QUOTES_PER_PROCESS))

        # 合约查询完成事件
        self._contracts_ready: threading.Event = threading.Event()
        self._exchanges_queried: int = 0

        # 启动 drain 线程
        self._start_drain()

        # 立即启动第一个 worker 进程（合约查询在 worker 内完成）
        self._allocate_process_slot()

        # 等待合约查询完成（3 个市场各返回 is_last=True）
        # 最多等 60s，超时不阻塞（非交易时段可能查不到合约）
        contracts_ok = self._contracts_ready.wait(timeout=60)
        if contracts_ok:
            self.write_log(
                f"合约查询完成，共 {len(symbol_contract_map)} 个合约，"
                f"服务器: {self._server_ip}:{self._server_port}"
            )
        else:
            self.write_log(
                f"合约查询等待超时（可能非交易时段），"
                f"已收到 {len(symbol_contract_map)} 个合约"
            )

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情：分配到有空位的子进程，满则起新进程"""
        if req.vt_symbol in self._subscribed:
            return

        exchange_id: int = EXCHANGE_VT2XTP.get(req.exchange, 0)
        if not exchange_id:
            self.write_log(f"不支持的交易所: {req.exchange}")
            return

        self._subscribed.add(req.vt_symbol)

        # 找有空位的 slot，或起新进程
        slot = self._allocate_process_slot()
        slot["symbols"].add(req.vt_symbol)
        slot["command_queue"].put({
            "action": CMD_SUBSCRIBE,
            "symbol": req.symbol,
            "exchange_id": exchange_id,
        })

    def close(self) -> None:
        """关闭网关"""
        # 停止 drain 线程
        self._drain_active = False
        if self._drain_thread and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=5)

        # 停止所有子进程
        self._stop_event.set()
        for slot in self._process_slots:
            try:
                slot["command_queue"].put({"action": CMD_STOP})
            except Exception:
                pass

        for slot in self._process_slots:
            process = slot["process"]
            if process.is_alive():
                process.join(timeout=10)
                if process.is_alive():
                    process.kill()

        # 清理队列
        for q in (self._tick_queue, self._bar_queue, self._log_queue):
            try:
                q.close()
                q.join_thread()
            except Exception:
                pass
        for slot in self._process_slots:
            cq = slot.get("command_queue")
            if cq is not None:
                try:
                    cq.close()
                    cq.join_thread()
                except Exception:
                    pass

        self._process_slots.clear()
        self._subscribed.clear()
        self.write_log("XTP Pro 行情网关已关闭")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _allocate_process_slot(self) -> Dict[str, Any]:
        """找有空位的子进程 slot，满则起新进程"""
        # 先清理已死进程的 slot
        self._prune_dead_slots()

        # 找有空位的
        for slot in self._process_slots:
            if len(slot["symbols"]) < self._max_quotes and slot["process"].is_alive():
                return slot

        # 所有 slot 满了或没有 slot，起新进程
        process_id = len(self._process_slots) + 1
        command_queue: mp.Queue = mp.Queue()
        self._stop_event.clear()

        process = mp.Process(
            target=_md_process_worker,
            args=(
                process_id,
                self._userid,
                self._password,
                self._client_id,
                self._server_ip,
                self._server_port,
                self._protocol,
                self._log_level,
                self._config_file,
                self._heartbeat_interval,
                self._local_ip,
                self._max_quotes,
                self._tick_queue,
                self._bar_queue,
                self._log_queue,
                command_queue,
                self._stop_event,
            ),
            name=f"XtpProMdProcess-{process_id}",
            daemon=True,
        )
        process.start()

        slot = {
            "process_id": process_id,
            "process": process,
            "command_queue": command_queue,
            "symbols": set(),
        }
        self._process_slots.append(slot)
        self.write_log(f"已启动行情子进程 #{process_id}")
        return slot

    def _prune_dead_slots(self) -> None:
        """清理已退出子进程的 slot"""
        i = 0
        while i < len(self._process_slots):
            slot = self._process_slots[i]
            if slot["process"].is_alive():
                i += 1
                continue
            dead_symbols = slot["symbols"]
            self._process_slots.pop(i)
            self._subscribed.difference_update(dead_symbols)
            self.write_log(
                f"检测到子进程 #{slot['process_id']} 已退出，回收 {len(dead_symbols)} 个订阅"
            )

    def _start_drain(self) -> None:
        """启动主进程 drain 线程"""
        self._drain_active = True
        self._drain_thread = threading.Thread(
            target=self._drain_loop,
            name="XtpProDrain",
            daemon=True,
        )
        self._drain_thread.start()

    def _drain_loop(self) -> None:
        """主进程 drain 循环：从队列消费行情数据，推入 EventEngine"""
        while self._drain_active:
            # 消费行情队列（tick 用于合约数据 + 原始 tick 推送）
            batch_count: int = 0
            while batch_count < QUEUE_DRAIN_BATCH_SIZE:
                try:
                    item = self._tick_queue.get_nowait()
                    self._process_tick_item(item)
                    batch_count += 1
                except Exception as exc:
                    if _normalize_queue_exception(exc):
                        break
                    raise

            # 消费 bar 队列（核心：分钟线推送）
            while True:
                try:
                    bar_item = self._bar_queue.get_nowait()
                    self._process_bar_item(bar_item)
                except Exception as exc:
                    if _normalize_queue_exception(exc):
                        break
                    raise

            # 消费日志队列
            while True:
                try:
                    msg: str = self._log_queue.get_nowait()
                    self.write_log(msg)
                except Exception as exc:
                    if _normalize_queue_exception(exc):
                        break
                    raise

            # 空闲时短暂休眠
            if batch_count == 0:
                time.sleep(QUEUE_DRAIN_IDLE_SLEEP)

    def _process_tick_item(self, item: Any) -> None:
        """处理队列中的单个项目"""
        if isinstance(item, TickData):
            # 行情数据（tick 仍推送，供需要原始 tick 的策略使用）
            self.on_tick(copy(item))
        elif isinstance(item, tuple) and len(item) == 3 and item[0] == "contract":
            # 合约数据
            _, contract, last = item
            symbol_contract_map[contract.vt_symbol] = contract
            self.on_contract(contract)

            # 追踪合约查询完成：每个市场查询返回 is_last=True
            # 3 个市场（SSE/SZSE/BSE）全部完成后通知 connect() 等待
            if last:
                self._exchanges_queried += 1
                if self._exchanges_queried >= len(EXCHANGE_XTP2VT):
                    self._contracts_ready.set()

    def _process_bar_item(self, item: Any) -> None:
        """处理 bar 队列中的项目，推 EVENT_BAR"""
        if isinstance(item, BarData):
            # 正常 bar 和补充 bar 统一走 on_bar
            self.on_bar(copy(item))

    # ------------------------------------------------------------------
    # 事件推送
    # ------------------------------------------------------------------

    def on_tick(self, tick: TickData) -> None:
        """推送 Tick 事件"""
        super().on_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """推送 Bar 事件

        与 vnpy on_tick 模式一致：推两个事件
        1) EVENT_BAR          — 通用，收所有 bar
        2) EVENT_BAR + vt_symbol — 特定合约，只收该合约的 bar
        """
        event1 = Event(EVENT_BAR, bar)
        self.event_engine.put(event1)

        event2 = Event(EVENT_BAR + bar.vt_symbol, bar)
        self.event_engine.put(event2)

    def on_contract(self, contract: ContractData) -> None:
        """推送 Contract 事件"""
        super().on_contract(contract)

    # ------------------------------------------------------------------
    # 交易接口占位（暂缓实现）
    # ------------------------------------------------------------------

    def send_order(self, req: OrderRequest) -> str:
        """发送委托（暂未实现）"""
        return ""

    def cancel_order(self, req: CancelRequest) -> None:
        """撤销委托（暂未实现）"""
        pass

    def query_account(self) -> None:
        """查询资金（暂未实现）"""
        pass

    def query_position(self) -> None:
        """查询持仓（暂未实现）"""
        pass


# ------------------------------------------------------------------
# 网关默认配置
# ------------------------------------------------------------------

def get_default_setting() -> Dict[str, Any]:
    """获取网关默认配置"""
    return {
        "用户名": "",
        "密码": "",
        "客户端ID": 1,
        "行情服务器": DEFAULT_SERVER,
        "行情端口": DEFAULT_PORT,
        "通讯协议": DEFAULT_PROTOCOL,
        "日志级别": DEFAULT_LOG_LEVEL,
        "配置文件": "",
        "心跳间隔": DEFAULT_HEARTBEAT_INTERVAL,
        "本地网卡IP": "",
        "每进程订阅数": DEFAULT_MAX_QUOTES_PER_PROCESS,
    }
