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
