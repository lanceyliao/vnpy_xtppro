"""
Bar 合成器

将 TickData 合成为分钟 BarData，支持：
- 实时 tick→bar 合成（分钟线）
- 缺失 bar 补充（盘中断连后自动补零 bar）
- 多合约并行处理
"""

import traceback
from copy import copy
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, TickData
from vnpy.trader.utility import ZoneInfo


DB_TZ = ZoneInfo("Asia/Shanghai")


class BarGenerator:
    """
    分钟线合成器

    架构：
    - update_tick: 每个 tick 到来时更新当前 bar
    - 当分钟切换时，推送上一分钟完成的 bar
    - 检测缺失 bar（断连导致），自动补充零量 bar
    - 通过 on_bar 回调推送合成后的 bar
    """

    def __init__(
        self,
        on_bar: Callable[[BarData], None],
        on_bar_gap: Optional[Callable[[BarData], None]] = None,
        write_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            on_bar: bar 推送回调（正常 + 补充的 bar 都会调用）
            on_bar_gap: 缺失 bar 补充回调（可选，用于告警）
            write_log: 日志回调（可选）
        """
        self.on_bar: Callable[[BarData], None] = on_bar
        self.on_bar_gap: Optional[Callable[[BarData], None]] = on_bar_gap
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
        - 如果 last_bar 存在且与当前 bar 间隔 > 1 分钟
          → 用 last_bar 的 close_price 构造零量补充 bar
        - 如果 last_bar 不存在
          → 从交易时间段起始时间开始补充（首根 bar 不补充）
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

                if minutes_diff > 1:
                    # 用 last_bar 的 close 构造补充 bar
                    # 从 last_bar 的下一分钟开始，到当前 bar 的前一分钟
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
                        # 推送补充 bar
                        self.on_bar(gap_bar)

                        # 缺失 bar 告警回调
                        if self.on_bar_gap:
                            self.on_bar_gap(gap_bar)

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
