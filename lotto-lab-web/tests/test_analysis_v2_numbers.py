from __future__ import annotations

import unittest

import analysis_v2


class NumbersNormalizationTests(unittest.TestCase):
    def test_numbers_returns_five_sorted_integers(self):
        self.assertEqual(analysis_v2._numbers({"numbers": [31, "8", 19, 2, 25]}), [2, 8, 19, 25, 31])

    def test_module_list_cannot_shadow_sorted_builtin(self):
        sentinel = analysis_v2.__dict__.get("sorted")
        had_value = "sorted" in analysis_v2.__dict__
        analysis_v2.sorted = []
        try:
            self.assertEqual(analysis_v2._numbers({"numbers": [5, 1, 4, 2, 3]}), [1, 2, 3, 4, 5])
        finally:
            if had_value:
                analysis_v2.sorted = sentinel
            else:
                del analysis_v2.sorted

    def test_invalid_number_still_raises(self):
        with self.assertRaises((TypeError, ValueError)):
            analysis_v2._numbers({"numbers": [1, 2, [], 4, 5]})


if __name__ == "__main__":
    unittest.main()
