"""
XTP Pro API 封装层

直接使用 xtp_pro_api_python 提供的 vnxtpxquote / vnxtpxtrader 预编译库，
无需自行编译 C++ 封装。
"""

from .xtp_pro_md_api import XtpProMdApi

__all__ = ["XtpProMdApi"]
