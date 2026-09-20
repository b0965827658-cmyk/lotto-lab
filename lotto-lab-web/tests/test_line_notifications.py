from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
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


def test_notifications_can_be_limited_to_selected_games_and_paused(tmp_path):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"U-tester"})
    assert "已開啟" in store.subscribe("U-tester")
    assert "已調整" in store.configure("U-tester", ["539", "六合彩"])
    assert store.recipients("tw539") == ["U-tester"]
    assert store.recipients("mark-six") == ["U-tester"]
    assert store.recipients("power-lottery") == []
    assert store.set_paused(True)
    assert store.recipients("tw539") == []
    assert store.set_paused(False)
    assert store.set_game_paused("tw539", True)
    assert store.recipients("tw539") == []
    assert store.recipients("mark-six") == ["U-tester"]


def test_delivery_claim_is_atomic_and_reuses_the_same_line_retry_key(tmp_path):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"U-tester"})
    first = store.claim_delivery("result:tw539:115000228", "U-tester")
    assert first
    assert store.claim_delivery("result:tw539:115000228", "U-tester") is None
    store.finish_delivery("result:tw539:115000228", "U-tester", sent=False)
    with store._connection() as connection:
        connection.execute("UPDATE deliveries SET updated_at=0 WHERE event_key=? AND user_id=?", ("result:tw539:115000228", "U-tester"))
    retry = store.claim_delivery("result:tw539:115000228", "U-tester")
    assert retry == first
    store.finish_delivery("result:tw539:115000228", "U-tester", sent=True)
    assert store.claim_delivery("result:tw539:115000228", "U-tester") is None


def test_concurrent_delivery_claims_have_one_winner_without_database_lock_errors(tmp_path):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"U-tester"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: store.claim_delivery("result:tw539:115000229", "U-tester"), range(8)))

    assert sum(result is not None for result in results) == 1


def test_taiwan_pre_draw_schedule_uses_taipei_time():
    monday = datetime(2026, 9, 21, 19, 31, tzinfo=ZoneInfo("Asia/Taipei"))
    assert set(taiwan_pre_draw_games(monday)) == {"tw539", "power-lottery", "daily-3", "daily-4"}
    assert taiwan_pre_draw_games(monday.replace(hour=19, minute=20)) == []
