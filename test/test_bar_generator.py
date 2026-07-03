"""
BarGenerator 单元测试

验证:
- tick→分钟线合成
- OHLCV 正确性
- 分钟切换推送
- 缺失 bar 补充（统一走 on_bar，volume=0 标识）
- 多合约并行
- force_finish_all
- 交易时段边界不补充
"""

import pytest
from datetime import datetime, timedelta
from copy import copy

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import TickData, BarData
from vnpy.trader.utility import ZoneInfo

from vnpy_xtppro.gateway.xtp_pro_gateway import BarGenerator


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def make_tick(
    symbol: str = "600000",
    exchange: Exchange = Exchange.SSE,
    dt: datetime = None,
    last_price: float = 10.0,
    volume: float = 1000.0,
    turnover: float = 10000.0,
    last_volume: float = 100.0,
    open_interest: float = 0.0,
) -> TickData:
    """构造测试用 TickData"""
    if dt is None:
        dt = datetime(2023, 7, 3, 10, 0, 0, tzinfo=CHINA_TZ)
    return TickData(
        symbol=symbol,
        exchange=exchange,
        datetime=dt,
        volume=volume,
        turnover=turnover,
        last_price=last_price,
        last_volume=last_volume,
        open_interest=open_interest,
        gateway_name="XTP_PRO",
    )


class TestBarGenerator:
    """BarGenerator 基础测试"""

    def setup_method(self):
        self.bars = []
        self.logs = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
            write_log=lambda msg: self.logs.append(msg),
        )

    def test_single_bar_from_ticks(self):
        """同一分钟内多个 tick 合成一根 bar"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 20, tzinfo=CHINA_TZ), last_price=10.5, last_volume=200)
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.2, last_volume=150)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 同一分钟内不推送 bar
        assert len(self.bars) == 0

        # 下一分钟的 tick 触发推送
        t4 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 0, tzinfo=CHINA_TZ), last_price=10.3, last_volume=50)
        self.bg.update_tick(t4)

        assert len(self.bars) == 1
        bar = self.bars[0]
        assert bar.open_price == 10.0
        assert bar.high_price == 10.5
        assert bar.low_price == 10.0
        assert bar.close_price == 10.2
        assert bar.volume == 450  # 100+200+150
        assert bar.interval == Interval.MINUTE

    def test_minute_switch_pushes_bar(self):
        """分钟切换时推送上一分钟 bar"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ))
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 2, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        assert len(self.bars) == 0

        self.bg.update_tick(t2)
        assert len(self.bars) == 1
        assert self.bars[0].datetime.minute == 0

        self.bg.update_tick(t3)
        assert len(self.bars) == 2
        assert self.bars[1].datetime.minute == 1

    def test_ohlcv_correctness(self):
        """验证 OHLCV 计算正确性"""
        ticks = [
            make_tick(dt=datetime(2023, 7, 3, 10, 0, 5, tzinfo=CHINA_TZ), last_price=100.0, last_volume=10, turnover=1000),
            make_tick(dt=datetime(2023, 7, 3, 10, 0, 15, tzinfo=CHINA_TZ), last_price=102.0, last_volume=20, turnover=3040),
            make_tick(dt=datetime(2023, 7, 3, 10, 0, 25, tzinfo=CHINA_TZ), last_price=98.0, last_volume=15, turnover=4510),
            make_tick(dt=datetime(2023, 7, 3, 10, 0, 45, tzinfo=CHINA_TZ), last_price=101.0, last_volume=25, turnover=7035),
        ]

        for t in ticks:
            self.bg.update_tick(t)

        # 触发推送
        self.bg.update_tick(make_tick(dt=datetime(2023, 7, 3, 10, 1, 0, tzinfo=CHINA_TZ)))

        bar = self.bars[0]
        assert bar.open_price == 100.0
        assert bar.high_price == 102.0
        assert bar.low_price == 98.0
        assert bar.close_price == 101.0
        assert bar.volume == 70  # 10+20+15+25


class TestBarGapFilling:
    """缺失 Bar 补充测试"""

    def setup_method(self):
        self.bars = []
        self.logs = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
            write_log=lambda msg: self.logs.append(msg),
        )

    def test_no_gap_when_consecutive(self):
        """连续分钟无缺失补充"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ))
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 2, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 无零量 gap bar
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 0

    def test_single_minute_gap(self):
        """1 分钟缺失（跳过 1 分钟）"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 跳过 10:02
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 3, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 10:03 tick 触发推送 10:01 bar + 补充 10:02
        t4 = make_tick(dt=datetime(2023, 7, 3, 10, 4, 30, tzinfo=CHINA_TZ), last_price=10.4)
        self.bg.update_tick(t4)

        # 检查是否有零量 gap bar（volume=0 标识补充 bar）
        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_minutes = [g.datetime.minute for g in gap_bars]
        assert 2 in gap_minutes, f"Expected gap at minute 2, got gaps at: {gap_minutes}"

    def test_multi_minute_gap(self):
        """多分钟缺失（断连 3 分钟）"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 跳过 10:02, 10:03, 10:04
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 5, 30, tzinfo=CHINA_TZ), last_price=10.5)
        t4 = make_tick(dt=datetime(2023, 7, 3, 10, 6, 30, tzinfo=CHINA_TZ), last_price=10.6)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 10:05 bar 推送时检测 10:01→10:05 缺失
        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_minutes = sorted([g.datetime.minute for g in gap_bars])
        assert gap_minutes == [2, 3, 4], f"Expected gaps at [2,3,4], got: {gap_minutes}"

        # 缺失 bar 应为零量
        for gap in gap_bars:
            assert gap.volume == 0
            assert gap.turnover == 0

        # 缺失 bar 的价格应为上一根 bar 的收盘价
        for gap in gap_bars:
            assert gap.open_price == 10.1
            assert gap.close_price == 10.1

    def test_gap_bar_datetime_correct(self):
        """缺失 bar 的时间戳正确"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ), last_price=10.1)
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 5, 30, tzinfo=CHINA_TZ), last_price=10.5)
        t4 = make_tick(dt=datetime(2023, 7, 3, 10, 6, 30, tzinfo=CHINA_TZ), last_price=10.6)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_dts = sorted([g.datetime for g in gap_bars])
        expected = [
            datetime(2023, 7, 3, 10, 2, 0, tzinfo=CHINA_TZ),
            datetime(2023, 7, 3, 10, 3, 0, tzinfo=CHINA_TZ),
            datetime(2023, 7, 3, 10, 4, 0, tzinfo=CHINA_TZ),
        ]
        assert gap_dts == expected

    def test_no_gap_across_sessions(self):
        """跨交易时段不补充（早盘→午盘）"""
        # 早盘 10:30 的 bar
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 29, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 30, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 午盘 13:00 的 tick（跳过了 10:31~12:59）
        t3 = make_tick(dt=datetime(2023, 7, 3, 13, 0, 30, tzinfo=CHINA_TZ), last_price=10.2)
        t4 = make_tick(dt=datetime(2023, 7, 3, 13, 1, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 不应产生跨时段的 gap bar
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 0, f"Should not gap-fill across sessions, got {len(gap_bars)} gap bars"

    def test_no_gap_across_days(self):
        """跨日不补充"""
        # 昨日 14:59 的 bar
        t1 = make_tick(dt=datetime(2023, 7, 3, 14, 58, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 14, 59, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 今日 9:30 的 tick
        t3 = make_tick(dt=datetime(2023, 7, 4, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.2)
        t4 = make_tick(dt=datetime(2023, 7, 4, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 不应产生跨日的 gap bar
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 0, f"Should not gap-fill across days, got {len(gap_bars)} gap bars"


class TestMultiSymbol:
    """多合约并行测试"""

    def setup_method(self):
        self.bars = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
        )

    def test_two_symbols_independent(self):
        """两个合约独立合成 bar"""
        t1 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=20.0)
        t3 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ), last_price=10.1)
        t4 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 10, 1, 30, tzinfo=CHINA_TZ), last_price=20.1)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 两个合约各推送 1 根 bar
        assert len(self.bars) == 2
        symbols = {b.vt_symbol for b in self.bars}
        assert symbols == {"600000.SSE", "000001.SZSE"}


class TestForceFinish:
    """force_finish_all 测试"""

    def setup_method(self):
        self.bars = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
        )

    def test_force_finish_pushes_pending_bar(self):
        """强制完成推送未完成的 bar"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ))
        self.bg.update_tick(t1)

        # 没有下一分钟的 tick，bar 未推送
        assert len(self.bars) == 0

        # 强制完成
        self.bg.force_finish_all()
        assert len(self.bars) == 1
        assert self.bars[0].datetime.minute == 0

    def test_force_finish_after_disconnect(self):
        """断连后强制完成所有合约的 bar"""
        t1 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)

        self.bg.force_finish_all()

        assert len(self.bars) == 2
