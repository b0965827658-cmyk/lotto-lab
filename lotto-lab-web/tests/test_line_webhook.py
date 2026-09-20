from __future__ import annotations

import base64
import hashlib
import hmac

import server


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
    assert "正在建立中" in server.line_message_reply(
        {"type": "message", "message": {"type": "text", "text": "管理"}, "source": {"userId": "admin-user"}}
    )


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
