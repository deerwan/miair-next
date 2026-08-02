"""通用默认封面解析。

两级兜底策略：
  1. 用户在 Web 配置的封面 URL (config.default_cover_url) —— 可定制
  2. 后端内置默认封面 http://{hostname}:{dlna_port}/default-cover —— 永远可用

DLNA 与 AirPlay 共用这一个解析函数，保证「一处配置，两个协议同时生效」。
"""
from __future__ import annotations

import os

# 内置默认封面资源 (与 device_server._handle_default_cover 读的文件一致)
_DEFAULT_COVER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "assets", "default_cover.png"
)


def default_cover_asset_path() -> str:
    return _DEFAULT_COVER_PATH


def resolve_default_cover_url(config, hostname: str, dlna_port: int) -> str:
    """解析最终使用的默认封面 URL。

    - 若用户配置了 default_cover_url 且非空，优先使用（可定制）。
    - 否则回退到后端内置默认封面地址（音箱可直接 HTTP GET）。
    """
    user_url = getattr(config, "default_cover_url", None)
    if user_url and user_url.strip():
        return user_url.strip()
    host = hostname or "127.0.0.1"
    return f"http://{host}:{dlna_port}/default-cover"
