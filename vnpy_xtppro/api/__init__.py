"""
XTP Pro API 封装层

meson 编译的 vnxtpxquote.so / vnxtpxtrader.so 安装在本目录下，
需要将本目录加入 sys.path 才能 import。
"""

import sys
from pathlib import Path

# 将 api 目录加入 sys.path，使 import vnxtpxquote / vnxtpxtrader 可用
_api_dir = str(Path(__file__).parent)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

# Linux: 同时设置 LD_LIBRARY_PATH，让 .so 找到 libxtpxquoteapi.so 等依赖
import os
if sys.platform == "linux":
    _ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    if _api_dir not in _ld_path:
        os.environ["LD_LIBRARY_PATH"] = (
            f"{_api_dir}:{_ld_path}" if _ld_path else _api_dir
        )

from .xtp_pro_md_api import XtpProMdApi

__all__ = ["XtpProMdApi"]
