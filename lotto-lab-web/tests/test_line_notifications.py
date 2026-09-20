from line_notifications import LineNotificationStore


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
