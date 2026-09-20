from marksix_official import history, latest


def test_marksix_accepts_only_completed_official_draw():
    payload = {
        "data": {
            "lotteryDraws": [
                {"id": "2026103N", "drawDate": "2026-09-22+08:00", "status": "Defined", "drawResult": {"drawnNo": [], "xDrawnNo": None}},
                {"id": "2026102N", "drawDate": "2026-09-19+08:00", "status": "Result", "drawResult": {"drawnNo": [5, 8, 11, 14, 19, 31], "xDrawnNo": 21}},
            ]
        }
    }
    item = latest(lambda: payload)

    assert item["period"] == "2026102N"
    assert item["numbers"] == [5, 8, 11, 14, 19, 31]
    assert item["bonus"] == [21]


def test_marksix_rejects_duplicate_or_unfinished_numbers():
    payload = {"data": {"lotteryDraws": [{"id": "bad", "drawDate": "2026-09-19", "status": "Result", "drawResult": {"drawnNo": [1, 1, 2, 3, 4, 5], "xDrawnNo": 6}}]}}
    try:
        latest(lambda: payload)
    except RuntimeError:
        pass
    else:
        raise AssertionError("invalid draw must fail closed")


def test_marksix_history_requires_complete_unique_official_rows_and_sorts_them():
    payload = {
        "data": {
            "lotteryDraws": [
                {"id": "2026100N", "drawDate": "2026-09-15+08:00", "status": "Result", "drawResult": {"drawnNo": [1, 2, 3, 4, 5, 6], "xDrawnNo": 7}},
                {"id": "2026102N", "drawDate": "2026-09-19+08:00", "status": "Result", "drawResult": {"drawnNo": [5, 8, 11, 14, 19, 31], "xDrawnNo": 21}},
            ]
        }
    }

    rows = history(2, fetcher=lambda: payload)

    assert [row["period"] for row in rows] == ["2026102N", "2026100N"]
