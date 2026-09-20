from taiwan_official_history import recent


def test_power_lottery_history_keeps_main_order_and_bonus():
    payload = {"rtCode": 0, "content": {"superLotto638Res": [{"period": 115000075, "lotteryDate": "2026-09-17T00:00:00", "drawNumberAppear": [28, 26, 31, 23, 37, 27, 7], "drawNumberSize": [23, 26, 27, 28, 31, 37, 7]}]}}
    rows = recent("power-lottery", month="2026-09", fetcher=lambda _url: payload)

    assert rows[0]["numbers"] == [28, 26, 31, 23, 37, 27]
    assert rows[0]["bonus"] == [7]


def test_daily_four_history_allows_repeated_digits_in_order():
    payload = {"rtCode": 0, "content": {"lotto4DHistoryRes": [{"period": 115000228, "lotteryDate": "2026-09-19T00:00:00", "drawNumberAppear": [4, 2, 3, 2]}]}}
    rows = recent("daily-4", month="2026-09", fetcher=lambda _url: payload)

    assert rows[0]["numbers"] == [4, 2, 3, 2]
