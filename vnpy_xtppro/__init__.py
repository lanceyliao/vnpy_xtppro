"""
VeighNa XTP Pro 行情网关

基于中泰证券 XTP Pro Python SDK (xtp_pro_api_python) 构建，
专注行情 MD 部分，支持独立进程架构。
"""

from .gateway import XtpProGateway, get_default_setting

__all__ = ["XtpProGateway", "get_default_setting"]
