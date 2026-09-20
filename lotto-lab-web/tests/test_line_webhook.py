from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error

import server
from line_notifications import LineNotificationStore


def test_line_signature_accepts_only_the_configured_secret(monkeypatch):
    monkeypatch.setattr(server, "LINE_CHANNEL_SECRET", "test-secret")
    body = b'{"events":[]}'
    signature = base64.b64encode(hmac.new(b"test-secret", body, hashlib.sha256).digest()).decode()

    assert server.line_signature_is_valid(body, signature)
    assert not server.line_signature_is_valid(body, "not-a-line-signature")


def test_line_help_reply_never_exposes_admin_operations(monkeypatch):
    monkeypatch.setattr(server, "LINE_ADMIN_USER_IDS", set())
    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "幫助"}, "source": {"userId": "ordinary-user"}}
    )

    assert reply is not None
    assert "管理" not in reply


def test_line_admin_command_requires_allowlist(monkeypatch):
    monkeypatch.setattr(server, "LINE_ADMIN_USER_IDS", {"admin-user"})

    assert "僅限管理員" in server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理"}, "source": {"userId": "ordinary-user"}}
    )
    assert "管理員選單" in server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理"}, "source": {"userId": "admin-user"}}
    )


def test_line_notification_guard_requires_known_staging_service_and_persistent_disk(tmp_path, monkeypatch):
    persistent_root = tmp_path / "persist"
    storage_file = persistent_root / "line.sqlite3"
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STAGING_PERSISTENT_ROOT", persistent_root)
    monkeypatch.setattr(server, "line_notification_persistent_mount_is_verified", lambda _root: True)

    assert server.line_notification_guard_reason(
        True,
        "staging",
        server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        storage_file,
        persistent_root,
    ) == "已啟用"
    assert server.line_notification_guard_reason(
        True, "staging", "other-service", storage_file, persistent_root
    ) == "測試站服務身分未通過驗證"
    assert server.line_notification_guard_reason(
        True,
        "staging",
        server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        tmp_path / "outside.sqlite3",
        persistent_root,
    ) == "通知資料未放在持久化磁碟"


def test_line_notification_guard_fails_closed_without_mount_token_or_tester(tmp_path, monkeypatch):
    persistent_root = tmp_path / "persist"
    storage_file = persistent_root / "line.sqlite3"
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STAGING_PERSISTENT_ROOT", persistent_root)
    monkeypatch.setattr(server, "line_notification_persistent_mount_is_verified", lambda _root: False)

    assert server.line_notification_guard_reason(
        True, "staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID, storage_file, persistent_root
    ) == "測試站持久化磁碟未掛載"

    monkeypatch.setattr(server, "line_notification_persistent_mount_is_verified", lambda _root: True)
    assert server.line_notification_guard_reason(
        True,
        "staging",
        server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        storage_file,
        persistent_root,
        has_access_token=False,
    ) == "LINE 發送憑證未設定"
    assert server.line_notification_guard_reason(
        True,
        "staging",
        server.LINE_NOTIFICATION_STAGING_SERVICE_ID,
        storage_file,
        persistent_root,
        has_tester_ids=False,
    ) == "尚未設定測試收件人"


def test_line_admin_can_pause_only_opted_in_staging_notifications(tmp_path, monkeypatch):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"admin-user"})
    assert "已開啟" in store.subscribe("admin-user")
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STORE", store)
    monkeypatch.setattr(server, "LINE_ADMIN_USER_IDS", {"admin-user"})
    monkeypatch.setattr(server, "LINE_NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_LOOP_ENABLED", True)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_BLOCK_REASON", "已啟用")

    status = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理 狀態"}, "source": {"userId": "admin-user"}}
    )
    paused = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理 通知 暫停"}, "source": {"userId": "admin-user"}}
    )
    assert paused is not None and "已暫停" in paused
    assert store.is_paused()
    assert store.recipients("tw539") == []
    resumed = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理 通知 恢復"}, "source": {"userId": "admin-user"}}
    )
    game_paused = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理 彩種 暫停 539"}, "source": {"userId": "admin-user"}}
    )

    assert status is not None and "已同意通知：1 人" in status
    assert resumed is not None and "已恢復" in resumed
    assert not store.is_paused()
    assert game_paused is not None and "今彩539" in game_paused
    assert store.is_game_paused("tw539")
    assert store.recipients("tw539") == []
    assert store.recipients("mark-six") == ["admin-user"]


def test_line_admin_cannot_override_the_notification_safety_lock(tmp_path, monkeypatch):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=False, tester_ids={"admin-user"})
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STORE", store)
    monkeypatch.setattr(server, "LINE_ADMIN_USER_IDS", {"admin-user"})
    monkeypatch.setattr(server, "LINE_NOTIFICATIONS_ENABLED", False)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_BLOCK_REASON", "非測試站模式")

    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理 通知 暫停"}, "source": {"userId": "admin-user"}}
    )

    assert reply is not None and "無法暫停" in reply
    assert not store.is_paused()


def test_line_retry_conflict_is_recorded_as_sent_without_a_duplicate(tmp_path, monkeypatch):
    store = LineNotificationStore(tmp_path / "line.sqlite3", enabled=True, tester_ids={"tester"})
    assert "已開啟" in store.subscribe("tester")
    monkeypatch.setattr(server, "LINE_NOTIFICATION_STORE", store)

    def already_accepted(*_args):
        raise urllib.error.HTTPError("https://api.line.me", 409, "conflict", {}, None)

    monkeypatch.setattr(server, "line_push", already_accepted)
    outcome = server.send_line_notification(
        "tw539",
        "result:tw539:115000230",
        "result",
        {"name": "今彩539", "period": "115000230", "date": "2026-09-20", "numbers": [1, 2, 3, 4, 5]},
    )

    assert outcome == {"sent": 1, "failed": 0}
    assert store.claim_delivery("result:tw539:115000230", "tester") is None


def test_invalid_latest_bonus_falls_back_to_validated_official_history(monkeypatch):
    server.cache.pop("taiwan-line-latest-power-lottery", None)
    payload = {
        "content": {
            "lastNumberList": [
                {
                    "gameCode": 5134,
                    "lotNumber": [1, 2, 3, 4, 5, 6, 9],
                    "period": "115000230",
                    "drawDate": "2026-09-20",
                }
            ]
        }
    }
    monkeypatch.setattr(server, "fetch_text", lambda *_args, **_kwargs: json.dumps(payload))
    monkeypatch.setattr(
        server,
        "taiwan_official_history_recent",
        lambda _game, limit: [
            {
                "period": "115000229",
                "date": "2026-09-19",
                "numbers": [1, 2, 3, 4, 5, 6],
                "bonus": [7],
                "source": "official",
                "sourceUrl": "https://official.example",
            }
        ],
    )

    latest = server.taiwan_line_latest("power-lottery")

    assert latest["officialHistoryFallback"] is True
    assert latest["bonus"] == [7]


def test_line_id_command_returns_only_the_callers_id():
    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "我的ID"}, "source": {"userId": "caller-only"}}
    )

    assert reply is not None
    assert "caller-only" in reply


def test_line_539_reply_uses_only_the_validated_official_adapter(monkeypatch):
    def official_latest(game):
        assert game == "tw539"
        return {
            "name": "今彩539",
            "period": "115000001",
            "date": "2026-09-20",
            "numbers": [1, 2, 3, 4, 5],
            "bonus": [],
            "bonusLabel": "",
        }

    def legacy_fallback():
        raise AssertionError("LINE 539 must not use the legacy fallback")

    monkeypatch.setattr(server, "taiwan_line_latest", official_latest)
    monkeypatch.setattr(server, "taiwan_latest", legacy_fallback)

    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "539"}, "source": {"userId": "ordinary-user"}}
    )

    assert reply is not None
    assert "01、02、03、04、05" in reply


def test_line_generic_latest_and_history_commands_use_the_requested_verified_game(monkeypatch):
    seen = []

    def latest_reply(game):
        seen.append(("latest", game))
        return f"latest:{game}"

    def recent(game, limit):
        seen.append(("history", game, limit))
        return [{"date": "2026-09-20", "numbers": [1, 2, 3, 4, 5], "bonus": []}]

    monkeypatch.setattr(server, "line_latest_reply", latest_reply)
    monkeypatch.setattr(server, "taiwan_official_history_recent", recent)

    latest = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "最新 威力彩"}, "source": {"userId": "ordinary-user"}}
    )
    history = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "歷史 539"}, "source": {"userId": "ordinary-user"}}
    )

    assert latest == "latest:power-lottery"
    assert history is not None and "01、02、03、04、05" in history
    assert seen == [("latest", "power-lottery"), ("history", "tw539", 5)]


def test_line_california_command_remains_verification_pending():
    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "最新 加州天天樂"}, "source": {"userId": "ordinary-user"}}
    )

    assert reply == server.DELIVERY_BLOCKED_MESSAGE


def test_line_marksix_history_uses_the_official_history_adapter(monkeypatch):
    monkeypatch.setattr(
        server,
        "marksix_official_history",
        lambda limit: [
            {
                "date": "2026-09-19",
                "numbers": [5, 8, 11, 14, 19, 31],
                "bonus": [21],
            }
        ],
    )

    reply = server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "歷史 六合彩"}, "source": {"userId": "ordinary-user"}}
    )

    assert reply is not None
    assert "六合彩 官方最近 1 期紀錄" in reply
    assert "05、08、11、14、19、31 + 21" in reply


def test_unverified_game_cannot_send_a_web_push_notification():
    outcome = server.notify_latest_game("ca-fantasy5")

    assert outcome["ok"] is False
    assert outcome["state"] == "verification_pending"
