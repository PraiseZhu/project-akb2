"""Calendar day used by briefing backstop / deadline scripts.

Both shell scripts pin DATE to `TZ=Asia/Shanghai date +%Y-%m-%d`. CI
runners are UTC, so `date.today()` is one day behind for eight hours
each night (16:00–24:00 UTC) and the tests write yesterday's briefing
file.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_today() -> str:
    return datetime.now(SHANGHAI).date().isoformat()
