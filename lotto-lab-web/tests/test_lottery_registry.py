from lottery_registry import catalog_rows, validate_main_numbers


def test_catalog_has_all_seven_requested_games():
    assert [row[1] for row in catalog_rows()] == [
        "tw539", "ca-fantasy5", "mark-six", "power-lottery", "lotto-649", "daily-3", "daily-4",
    ]


def test_draw_validation_rejects_wrong_size_duplicate_and_out_of_range_numbers():
    assert validate_main_numbers("tw539", [1, 2, 3, 4, 5])
    assert not validate_main_numbers("tw539", [1, 2, 3, 4])
    assert not validate_main_numbers("tw539", [1, 2, 3, 4, 4])
    assert validate_main_numbers("daily-3", [0, 5, 5])
    assert not validate_main_numbers("daily-3", [0, 5, 10])
