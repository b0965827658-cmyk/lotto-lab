from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import draw_sync


def at(value, zone="Asia/Taipei"):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo(zone))


def test_taiwan_draw_windows_follow_game_days():
    monday = set(draw_sync.due_games(at("2026-10-05T20:30:00")))
    assert monday == {"tw539", "daily-3", "daily-4", "power-lottery"}
    tuesday = set(draw_sync.due_games(at("2026-10-06T20:31:00")))
    assert tuesday == {"tw539", "daily-3", "daily-4", "lotto-649"}
    assert not set(draw_sync.TAIWAN_DAILY) & set(draw_sync.due_games(at("2026-10-04T20:31:00")))


def test_cross_timezone_windows_and_poll_rate():
    hk = at("2026-10-06T21:15:00", "Asia/Hong_Kong")
    assert "mark-six" in draw_sync.due_games(hk)
    assert "mark-six" in draw_sync.due_games(at("2026-10-04T21:30:00", "Asia/Hong_Kong"))
    california = at("2026-10-02T18:31:00", "America/Los_Angeles")
    assert "ca-fantasy5" in draw_sync.due_games(california)
    assert draw_sync.poll_seconds(california) == 30
    assert draw_sync.poll_seconds(at("2026-10-04T15:00:00")) == 900


def test_startup_sweeps_every_game():
    assert draw_sync.due_games(at("2026-10-04T10:00:00"), startup=True) == draw_sync.ALL_GAMES


def test_cycle_records_without_delivery_callback(tmp_path):
    store = draw_sync.DrawSyncStore(tmp_path / "sync.sqlite3")
    calls = []

    def fetch(game):
        calls.append(game)
        return {"game": game, "period": "p1", "date": "2026-10-05", "numbers": [1, 2, 3]}

    fetchers = {game: (lambda game=game: fetch(game)) for game in draw_sync.ALL_GAMES}
    result = draw_sync.run_cycle(store, fetchers, at("2026-10-05T10:00:00"), startup=True)
    assert set(result["updated"]) == set(draw_sync.ALL_GAMES)
    assert set(calls) == set(draw_sync.ALL_GAMES)
    assert all(item["ok"] for item in store.status().values())


def test_store_rejects_changed_numbers_for_same_draw(tmp_path):
    store = draw_sync.DrawSyncStore(tmp_path / "sync.sqlite3")
    draw = {"period": "p1", "date": "2026-10-05", "numbers": [1, 2, 3]}
    assert store.record("daily-3", draw, "2026-10-05T12:00:00+00:00")
    assert not store.record("daily-3", draw, "2026-10-05T12:01:00+00:00")
    with pytest.raises(ValueError, match="衝突"):
        store.record("daily-3", dict(draw, numbers=[4, 5, 6]), "2026-10-05T12:02:00+00:00")
