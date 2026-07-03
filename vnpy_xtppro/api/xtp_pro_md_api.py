"""
XTP Pro 行情 API 封装层

对接 xtp_pro_api_python 的 vnxtpxquote 模块，
适配 VeighNa 行情网关接口。

关键差异（XTP → XTP Pro）:
- onDepthMarketData 回调增加 bid1_qty/ask1_qty 队列参数
- data 字典新增 stk/opt/bond 嵌套字典（按 data_type_v2 区分）
- ticker_status 现为交易所原始值（不再转换）
- 新增 SetConfigFile / OnETFIOPVData / OnQueryAllNQTickersFullInfo 等
- 移除 GetTradingDay / SetUDPBufferSize
- QueryAllTickersPriceInfo 需要 exchange_id 参数
- CreateQuoteApi 新增 udpseq_output 参数
"""

import threading
from pathlib import Path
from typing import Any, Callable, Optional

from vnpy.trader.utility import get_folder_path


class XtpProMdApi:
    """
    XTP Pro 行情 API 封装

    继承 vnxtpxquote.QuoteApi，将 C++ 回调转为 Python 字典后
    通过回调函数推送给上层网关。
    """

    def __init__(self) -> None:
        """构造函数"""
        self._api: Any = None          # vnxtpxquote.QuoteApi 实例
        self._active: bool = False     # API 是否活跃
        self._thread: Optional[threading.Thread] = None  # API 工作线程

        # 连接参数
        self.userid: str = ""
        self.password: str = ""
        self.client_id: int = 0
        self.server_ip: str = ""
        self.server_port: int = 0
        self.protocol: int = 1         # 1=TCP, 2=UDP
        self.log_level: int = 3        # XTP_LOG_LEVEL_INFO
        self.config_file: str = ""     # XTP Pro 配置文件路径

        # 状态
        self.connect_status: bool = False
        self.login_status: bool = False

        # 回调函数（由上层网关注入）
        self.on_disconnected: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        self.on_sub_market_data: Optional[Callable] = None
        self.on_depth_market_data: Optional[Callable] = None
        self.on_query_all_tickers: Optional[Callable] = None
        self.on_tick_by_tick: Optional[Callable] = None

    # ------------------------------------------------------------------
    # 连接 / 登录
    # ------------------------------------------------------------------

    def connect(
        self,
        userid: str,
        password: str,
        client_id: int,
        server_ip: str,
        server_port: int,
        protocol: int = 1,
        log_level: int = 3,
        config_file: str = "",
        heartbeat_interval: int = 15,
        local_ip: str = "",
    ) -> None:
        """连接行情服务器

        Args:
            heartbeat_interval: 心跳间隔秒数，必须在Login前设置，默认15
            local_ip: 本地网卡IP，空串则传None让API自动选择；
                      注意：不能传"127.0.0.1"或空串给API
        """
        self.userid = userid
        self.password = password
        self.client_id = client_id
        self.server_ip = server_ip
        self.server_port = server_port
        self.protocol = protocol
        self.log_level = log_level
        self.config_file = config_file
        self.heartbeat_interval = heartbeat_interval
        self.local_ip = local_ip

        if not self.connect_status:
            self._create_api()
            self._login()

    def _create_api(self) -> None:
        """创建 QuoteApi 实例

        vnxtpxquote.so 由 meson 编译安装到 vnpy_xtppro/api/ 目录，
        需要将该目录加入 sys.path 才能 import。
        同时兼容旧版 libs/ 子目录布局。
        """
        import sys
        import os

        api_dir = Path(__file__).parent
        api_dir_str = str(api_dir)

        # 将 api 目录加入 sys.path（meson 编译的 .so 在这里）
        if api_dir_str not in sys.path:
            sys.path.insert(0, api_dir_str)

        # 兼容旧版 libs/ 子目录布局
        libs_dir = api_dir / "libs"
        if sys.platform == "linux":
            platform_dir = libs_dir / "linux_x86_64"
        elif sys.platform == "win32":
            platform_dir = libs_dir / "win64"
        else:
            platform_dir = None

        if platform_dir and platform_dir.exists():
            platform_str = str(platform_dir)
            if platform_str not in sys.path:
                sys.path.insert(0, platform_str)
            # Linux: 设置 LD_LIBRARY_PATH 让 .so 找到依赖
            if sys.platform == "linux":
                ld_path = os.environ.get("LD_LIBRARY_PATH", "")
                if platform_str not in ld_path:
                    os.environ["LD_LIBRARY_PATH"] = (
                        f"{platform_str}:{ld_path}" if ld_path else platform_str
                    )

        # Linux: 也把 api_dir 加到 LD_LIBRARY_PATH（libxtpxquoteapi.so 在这里）
        if sys.platform == "linux":
            ld_path = os.environ.get("LD_LIBRARY_PATH", "")
            if api_dir_str not in ld_path:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{api_dir_str}:{ld_path}" if ld_path else api_dir_str
                )

        try:
            from vnxtpxquote import QuoteApi
        except ImportError as e:
            raise ImportError(
                f"无法导入 vnxtpxquote: {e}\n"
                "请先编译 C++ 绑定：\n"
                "  cd api && meson setup builddir --prefix=/usr/local && ninja -C builddir install\n"
                "或确保 vnxtpxquote.so/.pyd 在 Python 搜索路径中"
            ) from e

        self._api = QuoteApi()
        path: Path = get_folder_path("xtppro")
        save_path = str(path).encode("GBK")

        # XTP Pro: CreateQuoteApi(client_id, save_file_path, log_level, udpseq_output=True)
        self._api.createQuoteApi(self.client_id, save_path, self.log_level, True)

        # XTP Pro 新增: 必须设置配置文件才能获取行情
        if self.config_file:
            self._api.setConfigFile(self.config_file)

        # 设置心跳间隔（必须在Login前调用）
        self._api.setHeartBeatInterval(self.heartbeat_interval)

    def _login(self) -> None:
        """登录行情服务器

        Login 返回值:
          0  = 登录成功
         -1  = 连接服务器出错
         -2  = 已存在连接（不允许重复登录）
         -3  = 输入有错误
        """
        # local_ip: 不能传空串""或"127.0.0.1"，None让API自动选择网卡
        local_ip_param = self.local_ip if self.local_ip else None

        n: int = self._api.login(
            self.server_ip,
            self.server_port,
            self.userid,
            self.password,
            self.protocol,
            local_ip_param,
        )

        if n == 0:
            self.connect_status = True
            self.login_status = True
        elif n == -2:
            # 已存在连接，视为已登录成功（重连场景）
            self.connect_status = True
            self.login_status = True
        else:
            error: dict = self._api.getApiLastError()
            raise ConnectionError(
                f"XTP Pro 行情服务器登录失败(返回值={n})，"
                f"代码：{error.get('error_id', -1)}，"
                f"信息：{error.get('error_msg', 'unknown')}"
            )

    def init(self) -> None:
        """启动 API 工作线程（开始接收回调）"""
        if not self._active:
            self._active = True
            self._thread = threading.Thread(
                target=self._api.init,
                name="XtpProMdApi",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        """关闭连接"""
        if self.connect_status:
            try:
                self._api.exit()
            except Exception:
                pass
        self.connect_status = False
        self.login_status = False
        self._active = False
        self._api = None

    # ------------------------------------------------------------------
    # 行情订阅
    # ------------------------------------------------------------------

    def subscribe_market_data(self, symbol: str, exchange_id: int) -> int:
        """订阅行情

        Args:
            symbol: 合约代码
            exchange_id: 交易所代码 (1=SH, 2=SZ)

        Returns:
            0 表示成功
        """
        if not self.login_status:
            return -1
        return self._api.subscribeMarketData(symbol, 1, exchange_id)

    def unsubscribe_market_data(self, symbol: str, exchange_id: int) -> int:
        """退订行情"""
        if not self.login_status:
            return -1
        return self._api.unSubscribeMarketData(symbol, 1, exchange_id)

    def subscribe_all_market_data(self, exchange_id: int = 0) -> int:
        """订阅全市场行情

        Args:
            exchange_id: 0=沪深全市场, 1=上海, 2=深圳
        """
        if not self.login_status:
            return -1
        return self._api.subscribeAllMarketData(exchange_id)

    def subscribe_tick_by_tick(self, symbol: str, exchange_id: int) -> int:
        """订阅逐笔行情"""
        if not self.login_status:
            return -1
        return self._api.subscribeTickByTick(symbol, 1, exchange_id)

    def subscribe_order_book(self, symbol: str, exchange_id: int) -> int:
        """订阅订单簿"""
        if not self.login_status:
            return -1
        return self._api.subscribeOrderBook(symbol, 1, exchange_id)

    # ------------------------------------------------------------------
    # 合约查询
    # ------------------------------------------------------------------

    def query_all_tickers(self, exchange_id: int) -> int:
        """查询合约部分静态信息"""
        if not self.login_status:
            return -1
        return self._api.queryAllTickers(exchange_id)

    def query_all_tickers_full_info(self, exchange_id: int) -> int:
        """查询合约完整静态信息"""
        if not self.login_status:
            return -1
        return self._api.queryAllTickersFullInfo(exchange_id)

    def query_all_nq_tickers_full_info(self) -> int:
        """查询北交所合约完整静态信息（XTP Pro 新增）"""
        if not self.login_status:
            return -1
        return self._api.queryAllNQTickersFullInfo()

    def query_all_tickers_price_info(self, exchange_id: int) -> int:
        """查询合约最新价格信息（XTP Pro 需要 exchange_id 参数）"""
        if not self.login_status:
            return -1
        return self._api.queryAllTickersPriceInfo(exchange_id)

    def query_tickers_latest_market_data(self, symbol: str, exchange_id: int) -> int:
        """查询合约最新快照信息（XTP Pro 新增）"""
        if not self.login_status:
            return -1
        return self._api.queryTickersLatestMarketData(symbol, 1, exchange_id)

    # ------------------------------------------------------------------
    # 其他接口
    # ------------------------------------------------------------------

    def get_api_version(self) -> str:
        """获取 API 版本号"""
        if self._api:
            return self._api.getApiVersion()
        return ""

    def get_api_last_error(self) -> dict:
        """获取最近一次 API 错误"""
        if self._api:
            return self._api.getApiLastError()
        return {}

    def set_heart_beat_interval(self, interval: int) -> None:
        """设置心跳间隔（秒），必须在 Login 前调用"""
        if self._api:
            self._api.setHeartBeatInterval(interval)

    def set_config_file(self, filename: str) -> None:
        """设置行情接收配置文件（XTP Pro 新增），必须在 Login 前调用"""
        if self._api:
            self._api.setConfigFile(filename)

    # ------------------------------------------------------------------
    # 回调方法（由 vnxtpxquote C++ 层调用）
    # ------------------------------------------------------------------

    def onDisconnected(self, reason: int) -> None:
        """连接断开回调"""
        self.connect_status = False
        self.login_status = False
        if self.on_disconnected:
            self.on_disconnected(reason)

    def onError(self, error: dict) -> None:
        """错误回调"""
        if self.on_error:
            self.on_error(error)

    def onSubMarketData(self, data: dict, error: dict, last: bool) -> None:
        """订阅行情应答"""
        if self.on_sub_market_data:
            self.on_sub_market_data(data, error, last)

    def onUnSubMarketData(self, data: dict, error: dict, last: bool) -> None:
        """退订行情应答"""
        pass  # 通常无需处理

    def onDepthMarketData(
        self,
        data: dict,
        bid1_qty_list: list,
        bid1_count: int,
        max_bid1_count: int,
        ask1_qty_list: list,
        ask1_count: int,
        max_ask1_count: int,
    ) -> None:
        """深度行情推送

        XTP Pro 版本与 XTP 的关键区别:
        - 增加 bid1_qty_list / ask1_qty_list 买一卖一队列
        - data 字典新增 stk/opt/bond 嵌套字典
        - data 新增 ticker_status / r1 字段
        """
        if self.on_depth_market_data:
            self.on_depth_market_data(
                data, bid1_qty_list, bid1_count, max_bid1_count,
                ask1_qty_list, ask1_count, max_ask1_count,
            )

    def onSubOrderBook(self, data: dict, error: dict, last: bool) -> None:
        """订阅订单簿应答"""
        pass

    def onUnSubOrderBook(self, data: dict, error: dict, last: bool) -> None:
        """退订订单簿应答"""
        pass

    def onOrderBook(self, data: dict) -> None:
        """订单簿推送"""
        pass

    def onSubTickByTick(self, data: dict, error: dict, last: bool) -> None:
        """订阅逐笔行情应答"""
        pass

    def onUnSubTickByTick(self, data: dict, error: dict, last: bool) -> None:
        """退订逐笔行情应答"""
        pass

    def onTickByTick(self, data: dict) -> None:
        """逐笔行情推送"""
        if self.on_tick_by_tick:
            self.on_tick_by_tick(data)

    def onSubscribeAllMarketData(self, exchange_id: int, error: dict) -> None:
        """全市场行情订阅应答"""
        pass

    def onUnSubscribeAllMarketData(self, exchange_id: int, error: dict) -> None:
        """全市场行情退订应答"""
        pass

    def onSubscribeAllOrderBook(self, exchange_id: int, error: dict) -> None:
        """全市场订单簿订阅应答"""
        pass

    def onUnSubscribeAllOrderBook(self, exchange_id: int, error: dict) -> None:
        """全市场订单簿退订应答"""
        pass

    def onSubscribeAllTickByTick(self, exchange_id: int, error: dict) -> None:
        """全市场逐笔行情订阅应答"""
        pass

    def onUnSubscribeAllTickByTick(self, exchange_id: int, error: dict) -> None:
        """全市场逐笔行情退订应答"""
        pass

    def onQueryAllTickers(self, data: dict, error: dict, last: bool) -> None:
        """查询合约回报"""
        if self.on_query_all_tickers:
            self.on_query_all_tickers(data, error, last)

    def onQueryTickersPriceInfo(self, data: dict, error: dict, last: bool) -> None:
        """查询合约价格回报"""
        pass

    def onSubscribeAllOptionMarketData(self, exchange_id: int, error: dict) -> None:
        """全市场期权行情订阅应答"""
        pass

    def onUnSubscribeAllOptionMarketData(self, exchange_id: int, error: dict) -> None:
        """全市场期权行情退订应答"""
        pass

    def onQueryAllTickersFullInfo(self, data: dict, error: dict, last: bool) -> None:
        """查询合约完整静态信息回报"""
        if self.on_query_all_tickers:
            self.on_query_all_tickers(data, error, last)

    # --- XTP Pro 新增回调 ---

    def onETFIOPVData(self, data: dict) -> None:
        """ETF IOPV 数据推送"""
        pass

    def onQueryAllNQTickersFullInfo(self, data: dict, error: dict, last: bool) -> None:
        """查询新三板合约完整静态信息回报"""
        if self.on_query_all_tickers:
            self.on_query_all_tickers(data, error, last)

    def onXTPQuoteNQFullInfo(self, data: dict) -> None:
        """新三板合约完整静态信息盘中推送"""
        pass

    def onQueryTickersLatestMarketData(self, data: dict, error: dict, last: bool) -> None:
        """查询合约最新快照回报"""
        pass

    def onSubscribeAllIndexPress(self, error: dict) -> None:
        """指数通行情订阅应答"""
        pass

    def onUnSubscribeAllIndexPress(self, error: dict) -> None:
        """指数通行情退订应答"""
        pass

    def onIndexPress(self, data: dict) -> None:
        """指数通行情推送"""
        pass

    def onSubscribeAllHKCMarketData(self, error: dict) -> None:
        """港股通行情订阅应答"""
        pass

    def onUnSubscribeAllHKCMarketData(self, error: dict) -> None:
        """港股通行情退订应答"""
        pass

    def onHKRLData(self, data: dict) -> None:
        """港股通实时额度推送"""
        pass

    def onHKCMarketData(self, data: dict) -> None:
        """港股通行情推送"""
        pass

    def onRequestRebuildQuote(self, data: dict) -> None:
        """回补行情结果回报"""
        pass

    def onRebuildTickByTick(self, data: dict) -> None:
        """回补逐笔行情推送"""
        pass

    def onRebuildMarketData(self, data: dict) -> None:
        """回补快照行情推送"""
        pass
