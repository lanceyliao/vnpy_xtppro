"""
XTP Pro 行情网关

VeighNa BaseGateway 适配层，驱动独立进程 MD worker，
通过 multiprocessing.Queue 消费行情数据并推入 EventEngine。
"""

import multiprocessing as mp
import queue
import threading
import time
from copy import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from vnpy.trader.constant import Exchange
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

# 自定义事件类型（VeighNa 核心未内置 EVENT_BAR）
EVENT_BAR = "eBar."
from vnpy.trader.utility import get_folder_path, ZoneInfo

from .xtp_pro_md import (
    CMD_STOP,
    CMD_SUBSCRIBE,
    CMD_UNSUBSCRIBE,
    EXCHANGE_VT2XTP,
    EXCHANGE_XTP2VT,
    LOGLEVEL_VT2XTP,
    PROTOCOL_VT2XTP,
    QUEUE_DRAIN_BATCH_SIZE,
    QUEUE_DRAIN_IDLE_SLEEP,
    _md_process_worker,
    _normalize_queue_exception,
    symbol_contract_map,
)


CHINA_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_SERVER = "122.112.252.150"
DEFAULT_PORT = 3002
DEFAULT_PROTOCOL = "TCP"
DEFAULT_LOG_LEVEL = "INFO"


class XtpProGateway(BaseGateway):
    """
    XTP Pro 行情网关

    使用独立进程运行 XTP Pro 行情 API，
    通过 multiprocessing.Queue 将行情数据传回主进程。
    """

    default_name: str = "XTP_PRO"

    def __init__(self, event_engine, gateway_name: str = "XTP_PRO") -> None:
        super().__init__(event_engine, gateway_name)

        # 子进程通信
        self._tick_queue: mp.Queue = mp.Queue()
        self._bar_queue: mp.Queue = mp.Queue()
        self._log_queue: mp.Queue = mp.Queue()
        self._command_queue: mp.Queue = mp.Queue()
        self._stop_event: mp.Event = mp.Event()

        # 子进程句柄
        self._process: Optional[mp.Process] = None

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

    # ------------------------------------------------------------------
    # BaseGateway 接口
    # ------------------------------------------------------------------

    def connect(self, setting: dict) -> None:
        """连接行情服务器"""
        self._userid = setting.get("用户名", "")
        self._password = setting.get("密码", "")
        self._client_id = int(setting.get("客户端ID", 1))
        self._server_ip = setting.get("行情服务器", DEFAULT_SERVER)
        self._server_port = int(setting.get("行情端口", DEFAULT_PORT))
        self._protocol = PROTOCOL_VT2XTP.get(setting.get("通讯协议", DEFAULT_PROTOCOL), 1)
        self._log_level = LOGLEVEL_VT2XTP.get(setting.get("日志级别", DEFAULT_LOG_LEVEL), 3)
        self._config_file = setting.get("配置文件", "")

        # 启动子进程
        self._start_process()

        # 启动 drain 线程
        self._start_drain()

        self.write_log(
            f"XTP Pro 行情网关已启动，服务器: {self._server_ip}:{self._server_port}"
        )

    def subscribe(self, req: SubscribeRequest) -> None:
        """订阅行情"""
        if req.vt_symbol in self._subscribed:
            return

        exchange_id: int = EXCHANGE_VT2XTP.get(req.exchange, 0)
        if not exchange_id:
            self.write_log(f"不支持的交易所: {req.exchange}")
            return

        self._subscribed.add(req.vt_symbol)
        self._command_queue.put({
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

        # 停止子进程
        self._stop_event.set()
        self._command_queue.put({"action": CMD_STOP})
        if self._process and self._process.is_alive():
            self._process.join(timeout=10)
            if self._process.is_alive():
                self._process.kill()

        self._process = None
        self._subscribed.clear()
        self.write_log("XTP Pro 行情网关已关闭")

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _start_process(self) -> None:
        """启动行情子进程"""
        self._stop_event.clear()

        self._process = mp.Process(
            target=_md_process_worker,
            args=(
                self._userid,
                self._password,
                self._client_id,
                self._server_ip,
                self._server_port,
                self._protocol,
                self._log_level,
                self._config_file,
                self._tick_queue,
                self._bar_queue,
                self._log_queue,
                self._command_queue,
                self._stop_event,
            ),
            name="XtpProMdProcess",
            daemon=True,
        )
        self._process.start()

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
            # 消费行情队列（tick 仅用于合约数据，行情走 bar 通道）
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

    def _process_bar_item(self, item: Any) -> None:
        """处理 bar 队列中的项目，推 EVENT_BAR"""
        if isinstance(item, BarData):
            # 正常 bar 或补充 bar
            self.on_bar(copy(item))
        elif isinstance(item, tuple) and len(item) == 2 and item[0] == "bar_gap":
            # 缺失 bar 告警（仍推送，策略可区分处理）
            _, bar = item
            self.on_bar(copy(bar))

    # ------------------------------------------------------------------
    # 事件推送
    # ------------------------------------------------------------------

    def on_tick(self, tick: TickData) -> None:
        """推送 Tick 事件"""
        super().on_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """推送 Bar 事件"""
        event = Event(EVENT_BAR + bar.vt_symbol, bar)
        self.event_engine.put(event)

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
    }
