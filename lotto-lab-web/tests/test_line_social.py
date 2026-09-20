from line_social import LineSocialStore


def make_store(tmp_path):
    return LineSocialStore(tmp_path / "social.sqlite3", enabled=True, tester_ids={"U-player"}, anonymous_key="test-key")


def test_social_share_is_anonymous_and_updates_same_daily_entry(tmp_path):
    store = make_store(tmp_path)

    first = store.share("U-player", "539", [1, 2, 3, 4, 5], "event-1")
    update = store.share("U-player", "539", [6, 7, 8, 9, 10], "event-2")

    assert "U-player" not in first
    assert "已分享" in first and "已分享" in update
    assert "06（1）" in store.hot_numbers("U-player", "539")
    assert "01（1）" not in store.hot_numbers("U-player", "539")
    assert "玩家-" in store.active_ranking("U-player", "539")


def test_social_rejects_invalid_or_duplicate_events_and_throttles_posts(tmp_path):
    store = make_store(tmp_path)

    assert "格式不正確" in store.share("U-player", "539", [1, 2, 3, 4, 4], "bad")
    assert "已分享" in store.share("U-player", "539", [1, 2, 3, 4, 5], "event-1")
    assert "不會重複" in store.share("U-player", "539", [1, 2, 3, 4, 5], "event-1")
    assert "已發" in store.post("U-player", "這一期我看好中溫號碼", "post-1")
    assert "一分鐘" in store.post("U-player", "第二則訊息", "post-2")
    assert "中溫號碼" in store.recent_posts("U-player")
