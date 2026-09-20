from pathlib import Path

from line_social import LineSocialStore


def test_social_store_keeps_player_ids_anonymous(tmp_path: Path):
    store = LineSocialStore(tmp_path / "social.json", enabled=True)

    reply = store.share("U-private-id", "539", [1, 2, 3, 4, 5])

    assert "U-private-id" not in reply
    assert "已分享" in reply
    assert "01（1）" in store.hot_numbers("539")
    assert "玩家-" in store.active_ranking("539")
    assert "已分享" in store.share("U-private-id", "539", [1, 2, 3, 4, 5], "event-1")
    assert "不會重複" in store.share("U-private-id", "539", [1, 2, 3, 4, 5], "event-1")


def test_social_store_rejects_invalid_share_and_limits_posts(tmp_path: Path):
    store = LineSocialStore(tmp_path / "social.json", enabled=True)

    assert "格式不正確" in store.share("U-player", "539", [1, 2, 3, 4, 4])
    assert "已發" in store.post("U-player", "  這一期我看好中溫號碼  ")
    assert "中溫號碼" in store.recent_posts()
