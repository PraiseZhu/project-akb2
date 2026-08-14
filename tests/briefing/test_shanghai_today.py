"""Lock the backstop calendar helper to Asia/Shanghai, not the process TZ."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tests.briefing.shanghai_today import shanghai_today


def test_shanghai_today_matches_script_date_in_utc_evening(monkeypatch):
    """CI runners are UTC. After 16:00 UTC the local date is yesterday in Shanghai.

    This is the window that turned main red on 2026-08-14T18:27Z: tests wrote
    `2026-08-14 Daily Briefing.md` while briefing_backstop.sh looked for
    `2026-08-15 Daily Briefing.md`.
    """

    frozen = datetime(2026, 8, 14, 18, 27, tzinfo=UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz is not None else frozen.replace(tzinfo=None)

    monkeypatch.setattr("tests.briefing.shanghai_today.datetime", _FrozenDateTime)

    assert shanghai_today() == "2026-08-15"
    assert shanghai_today() != frozen.date().isoformat()
    assert shanghai_today() == frozen.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
