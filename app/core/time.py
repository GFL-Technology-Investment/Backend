"""Tiện ích thời gian cho Việt Nam."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback
    ZoneInfo = None

from app.core.config import settings

VN_TZ = ZoneInfo(settings.timezone_name) if ZoneInfo else timezone(timedelta(hours=7))


def now_vn() -> str:
    return datetime.now(VN_TZ).strftime(settings.time_format)
