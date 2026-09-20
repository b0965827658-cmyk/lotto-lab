from datetime import datetime
from zoneinfo import ZoneInfo

from line_notifications import LineNotificationStore, taiwan_pre_draw_games


def test_notifications_fail_closed_until_staging_tester_is_enabled(tmp_path):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=False, tester_ids={"U-tester"})
    assert "尚未開放" in store.subscribe("U-tester")


def test_notifications_are_opt_in_and_event_idempotent(tmp_path):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"U-tester"})
    assert "已開啟" in store.subscribe("U-tester")
    assert "已開啟" in store.status("U-tester")
    assert store.record_webhook_event("evt-1")
    assert not store.record_webhook_event("evt-1")
    assert "已關閉" in store.unsubscribe("U-tester")


def test_taiwan_pre_draw_schedule_uses_taipei_time():
    monday = datetime(2026, 9, 21, 19, 31, tzinfo=ZoneInfo("Asia/Taipei"))
    assert set(taiwan_pre_draw_games(monday)) == {"tw539", "power-lottery", "daily-3", "daily-4"}
    assert taiwan_pre_draw_games(monday.replace(hour=19, minute=20)) == []
