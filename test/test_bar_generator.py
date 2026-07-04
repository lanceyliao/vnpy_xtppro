"""
BarGenerator 单元测试

验证:
- tick→分钟线合成
- OHLCV 正确性
- 分钟切换推送
- 缺失 bar 补充（统一走 on_bar，volume=0 标识）
- 开盘补 bar（session_start → 首根 bar 之间）
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
        dt = datetime(2023, 7, 3, 9, 30, 0, tzinfo=CHINA_TZ)
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
    """BarGenerator 基础测试

    注意：所有 tick 从 9:30 开始，避免触发开盘补 bar 逻辑。
    """

    def setup_method(self):
        self.bars = []
        self.logs = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
            write_log=lambda msg: self.logs.append(msg),
        )

    def test_single_bar_from_ticks(self):
        """同一分钟内多个 tick 合成一根 bar"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 20, tzinfo=CHINA_TZ), last_price=10.5, last_volume=200)
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.2, last_volume=150)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 同一分钟内不推送 bar
        assert len(self.bars) == 0

        # 下一分钟的 tick 触发推送
        t4 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 0, tzinfo=CHINA_TZ), last_price=10.3, last_volume=50)
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
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ))
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 32, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        assert len(self.bars) == 0

        self.bg.update_tick(t2)
        assert len(self.bars) == 1
        assert self.bars[0].datetime.minute == 30

        self.bg.update_tick(t3)
        assert len(self.bars) == 2
        assert self.bars[1].datetime.minute == 31

    def test_ohlcv_correctness(self):
        """验证 OHLCV 计算正确性"""
        ticks = [
            make_tick(dt=datetime(2023, 7, 3, 9, 30, 5, tzinfo=CHINA_TZ), last_price=100.0, last_volume=10, turnover=1000),
            make_tick(dt=datetime(2023, 7, 3, 9, 30, 15, tzinfo=CHINA_TZ), last_price=102.0, last_volume=20, turnover=3040),
            make_tick(dt=datetime(2023, 7, 3, 9, 30, 25, tzinfo=CHINA_TZ), last_price=98.0, last_volume=15, turnover=4510),
            make_tick(dt=datetime(2023, 7, 3, 9, 30, 45, tzinfo=CHINA_TZ), last_price=101.0, last_volume=25, turnover=7035),
        ]

        for t in ticks:
            self.bg.update_tick(t)

        # 触发推送
        self.bg.update_tick(make_tick(dt=datetime(2023, 7, 3, 9, 31, 0, tzinfo=CHINA_TZ)))

        bar = self.bars[0]
        assert bar.open_price == 100.0
        assert bar.high_price == 102.0
        assert bar.low_price == 98.0
        assert bar.close_price == 101.0
        assert bar.volume == 70  # 10+20+15+25


class TestBarGapFilling:
    """缺失 Bar 补充测试

    注意：所有 tick 从 9:30 开始，避免触发开盘补 bar 逻辑。
    """

    def setup_method(self):
        self.bars = []
        self.logs = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
            write_log=lambda msg: self.logs.append(msg),
        )

    def test_no_gap_when_consecutive(self):
        """连续分钟无缺失补充"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ))
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 32, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 无零量 gap bar
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 0

    def test_single_minute_gap(self):
        """1 分钟缺失（跳过 1 分钟）"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 跳过 9:32
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 33, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 9:33 tick 触发推送 9:31 bar + 补充 9:32
        t4 = make_tick(dt=datetime(2023, 7, 3, 9, 34, 30, tzinfo=CHINA_TZ), last_price=10.4)
        self.bg.update_tick(t4)

        # 检查是否有零量 gap bar（volume=0 标识补充 bar）
        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_minutes = [g.datetime.minute for g in gap_bars]
        assert 32 in gap_minutes, f"Expected gap at minute 32, got gaps at: {gap_minutes}"

    def test_multi_minute_gap(self):
        """多分钟缺失（断连 3 分钟）"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 跳过 9:32, 9:33, 9:34
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 35, 30, tzinfo=CHINA_TZ), last_price=10.5)
        t4 = make_tick(dt=datetime(2023, 7, 3, 9, 36, 30, tzinfo=CHINA_TZ), last_price=10.6)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 9:35 bar 推送时检测 9:31→9:35 缺失
        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_minutes = sorted([g.datetime.minute for g in gap_bars])
        assert gap_minutes == [32, 33, 34], f"Expected gaps at [32,33,34], got: {gap_minutes}"

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
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.1)
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 35, 30, tzinfo=CHINA_TZ), last_price=10.5)
        t4 = make_tick(dt=datetime(2023, 7, 3, 9, 36, 30, tzinfo=CHINA_TZ), last_price=10.6)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_dts = sorted([g.datetime for g in gap_bars])
        expected = [
            datetime(2023, 7, 3, 9, 32, 0, tzinfo=CHINA_TZ),
            datetime(2023, 7, 3, 9, 33, 0, tzinfo=CHINA_TZ),
            datetime(2023, 7, 3, 9, 34, 0, tzinfo=CHINA_TZ),
        ]
        assert gap_dts == expected

    def test_no_gap_across_sessions(self):
        """跨交易时段不补充（早盘→午盘）"""
        # 先从 9:30 开始建立首根 bar（避免触发开盘补）
        t0 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=9.0)
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 29, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 30, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 午盘 13:00 的 tick（跳过了 10:31~12:59）
        t3 = make_tick(dt=datetime(2023, 7, 3, 13, 0, 30, tzinfo=CHINA_TZ), last_price=10.2)
        t4 = make_tick(dt=datetime(2023, 7, 3, 13, 1, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t0)
        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 不应产生跨时段的 gap bar（10:31~12:59 不应补）
        gap_bars = [b for b in self.bars if b.volume == 0]
        # 9:30→10:29 之间会有盘中补 bar，但 10:30→13:00 之间不应补
        cross_session_gaps = [b for b in gap_bars if b.datetime.hour >= 11 or (b.datetime.hour == 10 and b.datetime.minute >= 31)]
        assert len(cross_session_gaps) == 0, f"Should not gap-fill across sessions, got {len(cross_session_gaps)} cross-session gap bars"

    def test_no_gap_across_days(self):
        """跨日不补充"""
        # 先从 9:30 开始建立首根 bar（避免触发开盘补）
        t0 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=9.0)
        t1 = make_tick(dt=datetime(2023, 7, 3, 14, 58, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(dt=datetime(2023, 7, 3, 14, 59, 30, tzinfo=CHINA_TZ), last_price=10.1)
        # 今日 9:30 的 tick
        t3 = make_tick(dt=datetime(2023, 7, 4, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.2)
        t4 = make_tick(dt=datetime(2023, 7, 4, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.3)

        self.bg.update_tick(t0)
        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 不应产生跨日的 gap bar（昨日 15:00 → 今日 9:30 之间不应补）
        gap_bars = [b for b in self.bars if b.volume == 0]
        # 检查没有日期为 7/3 但时间在 15:00 之后的 gap bar，也没有日期为 7/4 但时间在 9:30 之前的 gap bar
        cross_day_gaps = [b for b in gap_bars if b.datetime.date() == datetime(2023, 7, 4).date() and b.datetime.hour < 9]
        assert len(cross_day_gaps) == 0, f"Should not gap-fill across days, got {len(cross_day_gaps)} cross-day gap bars"


class TestOpeningGapFill:
    """开盘补 bar 测试

    场景：首根 bar 不是 session_start（9:30），需要补充 9:30 → 首根 bar 之间的缺失 bar。
    例如：9:45 才收到首根 bar，则补 9:30~9:44 共 15 根零量 bar。
    """

    def setup_method(self):
        self.bars = []
        self.logs = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
            write_log=lambda msg: self.logs.append(msg),
        )

    def test_opening_gap_fill_morning_session(self):
        """早盘开盘补 bar：首根 bar 在 10:00，补 9:30~9:59"""
        # 首根 bar 在 10:00（9:30~9:59 缺失）
        t1 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        t2 = make_tick(dt=datetime(2023, 7, 3, 10, 0, 30, tzinfo=CHINA_TZ), last_price=10.1, last_volume=50)
        # 触发推送
        t3 = make_tick(dt=datetime(2023, 7, 3, 10, 1, 0, tzinfo=CHINA_TZ), last_price=10.2, last_volume=30)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 应有：30 根开盘补 bar（9:30~9:59）+ 1 根 10:00 的真实 bar = 31 根
        assert len(self.bars) == 31, f"Expected 31 bars (30 gap + 1 real), got {len(self.bars)}"

        # 前 30 根是开盘补 bar（volume=0）
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 30, f"Expected 30 opening gap bars, got {len(gap_bars)}"

        # 开盘补 bar 的时间从 9:30 到 9:59
        gap_minutes = sorted([b.datetime.minute for b in gap_bars])
        assert gap_minutes == list(range(30, 60)), f"Expected minutes 30-59, got {gap_minutes}"

        # 开盘补 bar 的小时都是 9
        for gb in gap_bars:
            assert gb.datetime.hour == 9

        # 开盘补 bar 的价格都等于首根 bar 的 open_price
        for gb in gap_bars:
            assert gb.open_price == 10.0
            assert gb.close_price == 10.0
            assert gb.high_price == 10.0
            assert gb.low_price == 10.0

        # 最后一根是 10:00 的真实 bar
        real_bar = self.bars[-1]
        assert real_bar.volume == 150  # 100+50
        assert real_bar.datetime.hour == 10
        assert real_bar.datetime.minute == 0

    def test_opening_gap_fill_afternoon_session(self):
        """午盘开盘补 bar：首根 bar 在 13:05，补 13:00~13:04"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 13, 5, 10, tzinfo=CHINA_TZ), last_price=20.0, last_volume=200)
        # 触发推送
        t2 = make_tick(dt=datetime(2023, 7, 3, 13, 6, 0, tzinfo=CHINA_TZ), last_price=20.1, last_volume=50)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)

        # 应有：5 根开盘补 bar（13:00~13:04）+ 1 根 13:05 的真实 bar = 6 根
        assert len(self.bars) == 6, f"Expected 6 bars (5 gap + 1 real), got {len(self.bars)}"

        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 5

        gap_minutes = sorted([b.datetime.minute for b in gap_bars])
        assert gap_minutes == [0, 1, 2, 3, 4]

        for gb in gap_bars:
            assert gb.datetime.hour == 13
            assert gb.open_price == 20.0

    def test_no_opening_gap_when_at_session_start(self):
        """首根 bar 恰好在 session_start（9:30），不触发开盘补"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 31, 0, tzinfo=CHINA_TZ), last_price=10.1, last_volume=50)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)

        # 只有 1 根 9:30 的真实 bar，无开盘补
        assert len(self.bars) == 1
        assert self.bars[0].volume == 100
        assert self.bars[0].datetime.minute == 30

    def test_opening_gap_fill_uses_open_price(self):
        """开盘补 bar 使用首根 bar 的 open_price，不是 close_price"""
        # 首根 bar: open=10.0, close=10.5 (多个 tick)
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 32, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 32, 30, tzinfo=CHINA_TZ), last_price=10.5, last_volume=50)
        # 触发推送
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 33, 0, tzinfo=CHINA_TZ), last_price=10.6, last_volume=30)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)

        # 开盘补 bar（9:30, 9:31）使用 open_price=10.0
        gap_bars = [b for b in self.bars if b.volume == 0]
        assert len(gap_bars) == 2

        for gb in gap_bars:
            assert gb.open_price == 10.0, f"Gap bar should use open_price=10.0, got {gb.open_price}"
            assert gb.close_price == 10.0

    def test_opening_gap_then_mid_session_gap(self):
        """开盘补 + 盘中补同时发生"""
        # 首根 bar 在 9:35（开盘补 9:30~9:34）
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 35, 10, tzinfo=CHINA_TZ), last_price=10.0, last_volume=100)
        # 触发 9:35 bar 推送
        t2 = make_tick(dt=datetime(2023, 7, 3, 9, 36, 10, tzinfo=CHINA_TZ), last_price=10.1, last_volume=50)
        # 跳过 9:37, 9:38
        t3 = make_tick(dt=datetime(2023, 7, 3, 9, 39, 10, tzinfo=CHINA_TZ), last_price=10.3, last_volume=30)
        # 触发 9:39 bar 推送（盘中补 9:37, 9:38）
        t4 = make_tick(dt=datetime(2023, 7, 3, 9, 40, 10, tzinfo=CHINA_TZ), last_price=10.4, last_volume=20)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        gap_bars = [b for b in self.bars if b.volume == 0]
        gap_minutes = sorted([b.datetime.minute for b in gap_bars])

        # 开盘补: 9:30~9:34 (5根) + 盘中补: 9:37, 9:38 (2根) = 7根
        assert len(gap_bars) == 7, f"Expected 7 gap bars, got {len(gap_bars)} at minutes {gap_minutes}"

        # 开盘补的 bar 价格用 open_price=10.0
        opening_gap = [b for b in gap_bars if b.datetime.minute < 35]
        assert len(opening_gap) == 5
        for gb in opening_gap:
            assert gb.open_price == 10.0

        # 盘中补的 bar 价格用 last_bar.close_price=10.1
        mid_gap = [b for b in gap_bars if b.datetime.minute >= 37]
        assert len(mid_gap) == 2
        for gb in mid_gap:
            assert gb.open_price == 10.1


class TestMultiSymbol:
    """多合约并行测试

    注意：tick 从 9:30 开始，避免触发开盘补 bar。
    """

    def setup_method(self):
        self.bars = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
        )

    def test_two_symbols_independent(self):
        """两个合约独立合成 bar"""
        t1 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=10.0)
        t2 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ), last_price=20.0)
        t3 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ), last_price=10.1)
        t4 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 9, 31, 30, tzinfo=CHINA_TZ), last_price=20.1)

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)
        self.bg.update_tick(t3)
        self.bg.update_tick(t4)

        # 两个合约各推送 1 根 bar
        assert len(self.bars) == 2
        symbols = {b.vt_symbol for b in self.bars}
        assert symbols == {"600000.SSE", "000001.SZSE"}


class TestForceFinish:
    """force_finish_all 测试

    注意：tick 从 9:30 开始，避免触发开盘补 bar。
    """

    def setup_method(self):
        self.bars = []
        self.bg = BarGenerator(
            on_bar=lambda bar: self.bars.append(copy(bar)),
        )

    def test_force_finish_pushes_pending_bar(self):
        """强制完成推送未完成的 bar"""
        t1 = make_tick(dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ))
        self.bg.update_tick(t1)

        # 没有下一分钟的 tick，bar 未推送
        assert len(self.bars) == 0

        # 强制完成
        self.bg.force_finish_all()
        assert len(self.bars) == 1
        assert self.bars[0].datetime.minute == 30

    def test_force_finish_after_disconnect(self):
        """断连后强制完成所有合约的 bar"""
        t1 = make_tick(symbol="600000", dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ))
        t2 = make_tick(symbol="000001", exchange=Exchange.SZSE, dt=datetime(2023, 7, 3, 9, 30, 30, tzinfo=CHINA_TZ))

        self.bg.update_tick(t1)
        self.bg.update_tick(t2)

        self.bg.force_finish_all()

        assert len(self.bars) == 2
