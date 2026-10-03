import copy
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import fantasy5_stepzero as provider
import server

FIXTURE = json.loads((Path(__file__).parent / "fixtures/stepzero_ca_fantasy5.json").read_text(encoding="utf-8"))
NOW = datetime.fromisoformat("2026-09-27T10:02:36+00:00")


def payloads():
    return copy.deepcopy((FIXTURE["latest"], FIXTURE["history"]))


def request_fixture(path):
    return copy.deepcopy(FIXTURE["latest" if path == "/latest" else "history"])


def test_real_response_calendar_label_and_identity():
    current, draws = provider.validate(*payloads(), NOW)
    assert current == {"date": "2026-09-26", "numbers": [1,6,7,11,26], "providerDrawId": "25125087"}
    assert len(draws) == 10
    assert next(r for r in draws if r["date"] == "2026-09-20")["numbers"] == [1,8,10,19,21]


@pytest.mark.parametrize("mutation", ["state", "latest_game", "mismatch", "duplicate_number", "range", "duplicate_day", "duplicate_id", "gap", "schema_date", "anchor"])
def test_rejects_wrong_stale_or_conflicting_data(mutation):
    latest, history = payloads()
    if mutation == "state": history["game"]["jurisdiction_code"] = "FL"
    elif mutation == "latest_game": latest["game_id"] = "FL-FANTASY5"
    elif mutation == "mismatch": latest["latest_draw"]["result"] = "01 06 07 11 27"
    elif mutation == "duplicate_number": history["data"][0]["result"] = "01 01 07 11 26"
    elif mutation == "range": history["data"][0]["result"] = "00 06 07 11 26"
    elif mutation == "duplicate_day": history["data"][1]["draw_date"] = history["data"][0]["draw_date"]
    elif mutation == "duplicate_id": history["data"][1]["draw_id"] = history["data"][0]["draw_id"]
    elif mutation == "gap": history["data"].pop(1); history["count"] -= 1
    elif mutation == "schema_date": latest["latest_draw"]["draw_date"] = "2026-09-26T06:00:00.000Z"
    elif mutation == "anchor": history["data"][6]["result"] = "01 08 10 19 22"
    with pytest.raises(provider.FeedError): provider.validate(latest, history, NOW)
    with pytest.raises(provider.FeedError): provider.validate(*payloads(), datetime.fromisoformat("2026-09-29T10:00:00+00:00"))


@pytest.mark.parametrize("now,day,pending", [
    ("2026-03-08T09:00:00+00:00", "2026-03-07", False),
    ("2026-03-09T01:29:00+00:00", "2026-03-07", False),
    ("2026-03-09T01:35:00+00:00", "2026-03-07", True),
    ("2026-11-02T02:29:00+00:00", "2026-10-31", False),
    ("2026-11-02T02:35:00+00:00", "2026-10-31", True),
])
def test_pacific_dst_and_publication_window(now, day, pending):
    assert provider.freshness(day, datetime.fromisoformat(now))[0] == pending


def test_rejects_future_and_overdue_result():
    for day, now in [("2026-09-27", "2026-09-27T10:00:00+00:00"), ("2026-09-26", "2026-09-28T02:00:00+00:00")]:
        with pytest.raises(provider.FeedError): provider.freshness(day, datetime.fromisoformat(now))


def test_repeated_draw_does_not_rewrite_and_conflicts_rollback(tmp_path):
    path = tmp_path / "third_party.sqlite3"
    _, draws = provider.validate(*payloads(), NOW)
    assert provider.record_draws(path, draws, NOW.isoformat()) == 10
    original = path.read_bytes()
    assert provider.record_draws(path, draws, "a-later-fetch") == 0
    assert path.read_bytes() == original
    conflict = copy.deepcopy(draws)
    conflict[0]["numbers"] = [1,6,7,11,27]
    with pytest.raises(provider.FeedError): provider.record_draws(path, conflict, "another-fetch")
    assert path.read_bytes() == original
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*),COUNT(DISTINCT first_seen_at) FROM draws").fetchone() == (10,1)


def test_shared_cache_never_relabels_third_party_as_official(tmp_path):
    calls = []
    def request(path): calls.append(path); return request_fixture(path)
    feed = provider.Feed()
    result = feed.lookup(tmp_path / "cache.db", requester=request, now=NOW)
    assert result["ok"] and result["dataStatus"]["validated"] is False
    assert result["dataStatus"]["trust"] == "third_party"
    assert result["latest"]["period"] is None  # Provider ID is not an official draw number.
    assert result["notificationsEnabled"] is False
    result["latest"]["numbers"][0] = 39
    second = feed.lookup(tmp_path / "cache.db", requester=request, now=NOW)
    assert second["latest"]["numbers"] == [1,6,7,11,26]
    assert len(calls) == 2
    stale = feed.lookup(tmp_path / "cache.db", requester=request, now=datetime.fromisoformat("2026-09-28T02:00:00+00:00"))
    assert not stale["ok"] and "latest" not in stale


def test_regression_to_an_older_draw_is_not_published(tmp_path):
    path = tmp_path / "regression.db"
    _, draws = provider.validate(*payloads(), NOW)
    provider.record_draws(path, draws, NOW.isoformat())
    newer = {"date": "2026-09-27", "providerDrawId": "99999999", "numbers": [1,2,3,4,5]}
    provider.record_draws(path, [newer, *draws], "later")
    before = path.read_bytes()
    with pytest.raises(provider.FeedError): provider.record_draws(path, draws, "last")
    assert path.read_bytes() == before


def test_parallel_visitors_share_one_upstream_fetch(tmp_path):
    calls = []
    def request(path): calls.append(path); return request_fixture(path)
    feed = provider.Feed()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: feed.lookup(tmp_path / "parallel.db", requester=request, now=NOW), range(6)))
    assert all(result["ok"] for result in results)
    assert len(calls) == 2


def test_failure_cached_and_no_key_leak_or_stale_success(tmp_path):
    feed = provider.Feed()
    feed.lookup(tmp_path / "cache.db", requester=request_fixture, now=NOW)
    calls = []
    def fail(path): calls.append(path); raise RuntimeError("secret-api-key")
    result = feed.lookup(tmp_path / "cache.db", requester=fail, now=NOW, force=True)
    assert not result["ok"] and "latest" not in result and "secret-api-key" not in json.dumps(result)
    feed.lookup(tmp_path / "cache.db", requester=fail, now=NOW)
    assert len(calls) == 1


@pytest.mark.parametrize("runtime,service,expected,source", [
    ("staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID, 200, ""),
    ("production", server.LINE_NOTIFICATION_STAGING_SERVICE_ID, 409, "stepzero"),
    ("staging", "other-service", 409, "stepzero"),
    ("staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID, 200, "stepzero"),
    ("staging", server.LINE_NOTIFICATION_STAGING_SERVICE_ID, 503, "official"),
])
def test_http_only_designated_staging_and_no_line(monkeypatch, tmp_path, runtime, service, expected, source):
    monkeypatch.setattr(server, "LINE_NOTIFICATION_RUNTIME", runtime)
    monkeypatch.setattr(server, "LINE_NOTIFICATION_SERVICE_ID", service)
    monkeypatch.setattr(server, "PERSISTENT_DATA", tmp_path)
    monkeypatch.setattr(provider, "configured", lambda: True)
    monkeypatch.setattr(server.california_fantasy5, "probe", lambda: {"ok": False, "attempts": []})
    feed = provider.Feed()
    monkeypatch.setattr(provider.feed, "lookup", lambda path: feed.lookup(path, requester=request_fixture, now=NOW))
    monkeypatch.setattr(server, "california_latest", lambda: pytest.fail("legacy source forbidden"))
    monkeypatch.setattr(server, "line_push", lambda *a, **k: pytest.fail("LINE forbidden"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    try:
        try: response = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}/api/latest?game=ca-fantasy5&source={source}")
        except urllib.error.HTTPError as exc: response = exc
        with response:
            body = json.load(response)
            assert response.status == expected
        if expected == 200:
            assert body["latest"]["numbers"] == [1,6,7,11,26]
            assert body["dataStatus"]["validated"] is False
        else: assert "latest" not in body and not list(tmp_path.iterdir())
        assert server.delivery_is_blocked("ca-fantasy5")
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join()
