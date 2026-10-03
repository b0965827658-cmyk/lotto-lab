from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import server
from line_notifications import LineNotificationStore
from pathlib import Path


def test_notification_disk_matches_the_confirmed_render_staging_mount(monkeypatch):
    root = Path("/var/data/lotto-lab")
    assert server.LINE_NOTIFICATION_STAGING_PERSISTENT_ROOT == root
    monkeypatch.setattr(server, "line_notification_persistent_mount_is_verified", lambda _root: True)
    assert server.line_notification_guard_reason(True, "staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        root / "line_notifications_staging.sqlite3", root) == "已啟用"
    wrong = Path("/api/health")
    assert server.line_notification_guard_reason(True, "staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        wrong / "line.sqlite3", wrong) == "通知資料位置未通過驗證"


@pytest.fixture
def armed(tmp_path, monkeypatch):
    root = tmp_path / "disk"
    store = LineNotificationStore(root / "line.sqlite3", enabled=True, tester_ids={"admin", "outsider"})
    store.subscribe("admin")
    store.subscribe("outsider")  # Simulate an old database with a non-admin tester.
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STORE", store)
    monkeypatch.setattr(server, "LINE_ADMIN_USER_IDS", {"admin"})
    monkeypatch.setattr(server, "LINE_NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_RUNTIME", "staging")
    monkeypatch.setattr(server, "LINE_NOTIFICATION_SERVICE_ID", server.LINE_NOTIFICATION_STAGING_SERVICE_ID)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_FILE", store.path)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_PERSISTENT_ROOT", root)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STAGING_PERSISTENT_ROOT", root)
    monkeypatch.setattr(server, "line_notification_persistent_mount_is_verified", lambda _root: True)
    monkeypatch.setattr(server, "LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    return store


@pytest.fixture
def result():
    return {"game": "tw539", "name": "今彩539", "period": "115000230", "date": "2026-09-21",
            "numbers": [1, 2, 3, 4, 5], "bonus": [], "sourceUrl": server.TAIWAN_LAST_URL}


def test_only_admin_receives_once_across_restart(armed, result, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "line_push", lambda *args: calls.append(args))
    assert server.send_line_notification("tw539", "result:tw539:115000230", "result", result) == {"sent": 1, "failed": 0}
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STORE", LineNotificationStore(armed.path, enabled=True, tester_ids={"admin", "outsider"}))
    assert server.send_line_notification("tw539", "result:tw539:115000230", "result", result) == {"sent": 0, "failed": 0}
    assert len(calls) == 1 and calls[0][0] == "admin"


@pytest.mark.parametrize("attribute,value", [("LINE_NOTIFICATION_RUNTIME", "production"),
    ("LINE_NOTIFICATION_SERVICE_ID", "production-service"), ("LINE_NOTIFICATIONS_ENABLED", False),
    ("LINE_ADMIN_USER_IDS", set()), ("LINE_CHANNEL_ACCESS_TOKEN", "")])
def test_send_and_transport_fail_closed(armed, result, monkeypatch, attribute, value):
    monkeypatch.setattr(server, attribute, value)
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("unexpected network send"))
    with pytest.raises(RuntimeError):
        server.send_line_notification("tw539", "result:tw539:115000230", "result", result)
    with pytest.raises(RuntimeError):
        server.line_push("admin", "test", "retry")


def test_transport_rejects_non_admin(armed, monkeypatch):
    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("unexpected network send"))
    with pytest.raises(RuntimeError):
        server.line_push("outsider", "test", "retry")


@pytest.mark.parametrize("changes", [
    {"sourceUrl": "https://example.com/result"}, {"sourceUrl": "https://api.taiwanlottery.com.evil.test/result"},
    {"sourceUrl": "http://api.taiwanlottery.com/TLCAPIWeB/Lottery/LastNumber"},
    {"game": "daily-3"}, {"date": "2026-02-30"}, {"period": "different"},
    {"numbers": [1, 1, 2, 3, 4]}, {"numbers": [True, 2, 3, 4, 5]},
    {"numbers": [1, 2, 3, 4], "bonus": [5]},
])
def test_invalid_results_do_not_claim_or_send(armed, result, monkeypatch, changes):
    result.update(changes)
    monkeypatch.setattr(server, "line_push", lambda *_a: pytest.fail("unexpected push"))
    with pytest.raises((ValueError, RuntimeError)):
        server.send_line_notification("tw539", "result:tw539:115000230", "result", result)
    with armed._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


def test_pre_draw_cannot_be_reenabled(armed, result, monkeypatch):
    monkeypatch.setattr(server, "LINE_NOTIFICATION_PRE_DRAW_ENABLED", True)
    monkeypatch.setattr(server, "marksix_latest", lambda: {"date": "2026-09-20"})
    monkeypatch.setattr(server, "line_push", lambda *_a: pytest.fail("unexpected push"))
    with pytest.raises(ValueError):
        server.send_line_notification("tw539", "pre:tw539:2026-09-21", "pre", result)
    assert server.run_line_notification_cycle(datetime(2026, 9, 21, 19, 31, tzinfo=ZoneInfo("Asia/Taipei"))) == {"ok": True, "pre": 0, "result": 0, "failed": 0}


def test_cycle_rejects_stale_or_unavailable_official_results(armed, result, monkeypatch):
    armed.configure("admin", ["539"])
    armed.unsubscribe("outsider")
    monkeypatch.setattr(server, "line_push", lambda *_a: pytest.fail("unexpected push"))
    monkeypatch.setattr(server, "taiwan_line_latest", lambda _game: dict(result, date="2026-09-20"))
    now = datetime(2026, 9, 21, 21, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert server.run_line_notification_cycle(now)["result"] == 0
    def unavailable(_game):
        raise RuntimeError("official verification failed")
    monkeypatch.setattr(server, "taiwan_line_latest", unavailable)
    assert server.run_line_notification_cycle(now) == {"ok": True, "pre": 0, "result": 0, "failed": 1}


def test_fantasy5_blocked_even_for_direct_send(armed, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "line_push", lambda *args: calls.append(args))
    monkeypatch.setattr(server, "validate_line_notification_result", lambda *args: None)
    for _ in range(2):
        with pytest.raises(ValueError, match="delivery is blocked"):
            server.send_line_notification("ca-fantasy5", "result:ca-fantasy5:12006", "result", {})
    assert calls == []
