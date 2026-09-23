from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import california_fantasy5_official as f5


class Fantasy5OfficialTests(unittest.TestCase):
    def test_valid_numbers(self):
        self.assertEqual(f5.validate_numbers([1, 8, 10, 19, 21]), (1, 8, 10, 19, 21))

    def test_rejects_duplicate_partial_and_out_of_range(self):
        for values in ([1, 2, 3, 4], [1, 1, 3, 4, 5], [1, 2, 3, 4, 40]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                f5.validate_numbers(values)

    def test_discovers_game_by_name_not_guessed_id(self):
        game = f5.discover_fantasy5({"drawGames": [{"name": "Powerball"}, {"name": "Fantasy 5", "number": 99}]})
        self.assertEqual(game["number"], 99)

    def test_historical_fixture_12006(self):
        payload = {"games": [{"name": "Fantasy 5", "draws": [{"drawNumber": 12006, "drawDate": "2026-09-20", "winningNumbers": [1, 8, 10, 19, 21]}]}]}
        game = f5.discover_fantasy5(payload)
        draw = f5._latest_draw(game)
        self.assertEqual(f5.validate_numbers(draw["winningNumbers"]), (1, 8, 10, 19, 21))

    def test_dst_safe_fast_window(self):
        la = ZoneInfo("America/Los_Angeles")
        self.assertTrue(f5.in_fast_poll_window(datetime(2026, 9, 21, 18, 30, tzinfo=la)))
        self.assertFalse(f5.in_fast_poll_window(datetime(2026, 9, 21, 17, 30, tzinfo=la)))


if __name__ == "__main__":
    unittest.main()
