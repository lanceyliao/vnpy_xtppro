"""
XtpProMdApi 单元测试

测试 API 封装层的基本功能，不需要实际连接。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vnpy_xtppro.api.xtp_pro_md_api import XtpProMdApi


def test_api_instantiation():
    """测试 API 实例化"""
    api = XtpProMdApi()
    assert api._api is None
    assert api._active is False
    assert api.connect_status is False
    assert api.login_status is False
    print("✓ API 实例化测试通过")


def test_exchange_mapping():
    """测试交易所映射"""
    from vnpy_xtppro.gateway.xtp_pro_gateway import EXCHANGE_XTP2VT, EXCHANGE_VT2XTP
    from vnpy.trader.constant import Exchange

    assert EXCHANGE_XTP2VT[1] == Exchange.SSE
    assert EXCHANGE_XTP2VT[2] == Exchange.SZSE
    assert EXCHANGE_XTP2VT[3] == Exchange.BSE
    assert EXCHANGE_VT2XTP[Exchange.SSE] == 1
    assert EXCHANGE_VT2XTP[Exchange.SZSE] == 2
    assert EXCHANGE_VT2XTP[Exchange.BSE] == 3
    print("✓ 交易所映射测试通过（含北交所）")


def test_protocol_mapping():
    """测试协议映射"""
    from vnpy_xtppro.gateway.xtp_pro_gateway import PROTOCOL_VT2XTP
    assert PROTOCOL_VT2XTP["TCP"] == 1
    assert PROTOCOL_VT2XTP["UDP"] == 2
    print("✓ 协议映射测试通过")


def test_loglevel_mapping():
    """测试日志级别映射"""
    from vnpy_xtppro.gateway.xtp_pro_gateway import LOGLEVEL_VT2XTP
    assert LOGLEVEL_VT2XTP["INFO"] == 3
    assert LOGLEVEL_VT2XTP["DEBUG"] == 4
    print("✓ 日志级别映射测试通过")


def test_default_setting():
    """测试默认配置"""
    from vnpy_xtppro.gateway.xtp_pro_gateway import get_default_setting
    setting = get_default_setting()
    assert "用户名" in setting
    assert "行情服务器" in setting
    assert "心跳间隔" in setting
    assert "本地网卡IP" in setting
    assert setting["行情端口"] == 3002
    assert setting["心跳间隔"] == 15
    print("✓ 默认配置测试通过")


if __name__ == "__main__":
    test_api_instantiation()
    test_exchange_mapping()
    test_protocol_mapping()
    test_loglevel_mapping()
    test_default_setting()
    print("\n所有单元测试通过 ✓")
