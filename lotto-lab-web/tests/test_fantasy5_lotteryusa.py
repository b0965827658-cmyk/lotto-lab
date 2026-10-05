from datetime import datetime, timezone

import pytest

import fantasy5_lotteryusa as provider


def page(rows):
    body = "CA Fantasy 5 numbers from LotteryUSA\n\n" + "\n\n".join(
        f"{day}\n{numbers}\n\nEst. jackpot: $80,000 " for day, numbers in rows
    )
    return f'<textarea class="c-copy-to-clipboard__input">{body}</textarea>'


ROWS = [
    ("Friday, Oct 02, 2026", "17, 25, 26, 28, 39"),
    ("Thursday, Oct 01, 2026", "13, 19, 23, 28, 31"),
    ("Wednesday, Sep 30, 2026", "1, 2, 22, 37, 39"),
    ("Tuesday, Sep 29, 2026", "11, 24, 30, 33, 39"),
    ("Monday, Sep 28, 2026", "8, 10, 12, 14, 24"),
    ("Sunday, Sep 27, 2026", "3, 15, 20, 31, 39"),
    ("Saturday, Sep 26, 2026", "1, 6, 7, 11, 26"),
    ("Friday, Sep 25, 2026", "6, 15, 22, 33, 36"),
    ("Thursday, Sep 24, 2026", "12, 15, 17, 29, 31"),
    ("Wednesday, Sep 23, 2026", "5, 11, 12, 19, 38"),
    ("Tuesday, Sep 22, 2026", "4, 5, 25, 27, 36"),
    ("Monday, Sep 21, 2026", "3, 5, 13, 20, 36"),
    ("Sunday, Sep 20, 2026", "1, 8, 10, 19, 21"),
]


def test_parse_strict_export_and_anchor():
    draws = provider.parse(page(ROWS))
    assert draws[0]["date"] == "2026-10-02"
    assert draws[0]["numbers"] == [17, 25, 26, 28, 39]


@pytest.mark.parametrize("replacement", [
    "17, 17, 26, 28, 39",
    "17, 25, 26, 28, 40",
    "25, 17, 26, 28, 39",
])
def test_parse_rejects_invalid_numbers(replacement):
    rows = [(ROWS[0][0], replacement), *ROWS[1:]]
    with pytest.raises(provider.FeedError):
        provider.parse(page(rows))


def test_parse_rejects_missing_day_and_anchor_conflict():
    with pytest.raises(provider.FeedError, match="不連續"):
        provider.parse(page([ROWS[0], *ROWS[2:]]))
    bad = [*ROWS[:-1], (ROWS[-1][0], "1, 8, 10, 19, 22")]
    with pytest.raises(provider.FeedError, match="錨點"):
        provider.parse(page(bad))


def test_lookup_records_and_rejects_later_conflict(tmp_path):
    now = datetime(2026, 10, 3, 3, tzinfo=timezone.utc)
    feed = provider.Feed()
    first = feed.lookup(tmp_path / "ledger.sqlite3", requester=lambda: page(ROWS), now=now, force=True)
    assert first["ok"] is True
    assert first["latest"]["source"] == "LotteryUSA（第三方）"
    changed = [(ROWS[0][0], "16, 25, 26, 28, 39"), *ROWS[1:]]
    conflict = feed.lookup(tmp_path / "ledger.sqlite3", requester=lambda: page(changed), now=now, force=True)
    assert conflict["ok"] is False
    assert "衝突" in conflict["error"]


def test_lookup_fails_closed_when_stale(tmp_path):
    feed = provider.Feed()
    result = feed.lookup(tmp_path / "ledger.sqlite3", requester=lambda: page(ROWS),
                         now=datetime(2026, 10, 5, 3, tzinfo=timezone.utc), force=True)
    assert result["ok"] is False
    assert result["dataStatus"]["state"] == "unavailable"


def test_request_refuses_redirect(monkeypatch):
    class Response:
        status = 302
        headers = type("Headers", (), {"get_content_type": lambda self: "text/html"})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(provider.urllib.request, "build_opener", lambda handler: Opener())
    with pytest.raises(provider.FeedError, match="有效網頁"):
        provider._request()
