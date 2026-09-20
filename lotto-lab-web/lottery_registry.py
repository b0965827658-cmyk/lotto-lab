"""Staging-only registry for LINE lottery delivery.

This module deliberately contains no prediction logic and no network fetching. A
game becomes available to LINE only after its source adapter validates its draw
rules and provenance.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LotteryGame:
    code: str
    name: str
    country: str
    draw_size: int
    min_number: int
    max_number: int
    distinct_numbers: bool
    status: str
    source_status: str


GAMES: tuple[LotteryGame, ...] = (
    LotteryGame("tw539", "今彩539", "台灣", 5, 1, 39, True, "已連線，資料驗證中", "official-adapter-pending"),
    LotteryGame("ca-fantasy5", "加州天天樂", "美國加州", 5, 1, 39, True, "已連線，資料驗證中", "official-adapter-pending"),
    LotteryGame("mark-six", "六合彩", "香港", 6, 1, 49, True, "資料來源驗證中", "not-started"),
    LotteryGame("power-lottery", "威力彩", "台灣", 6, 1, 38, True, "官方最新開獎已接通，歷史資料驗證中", "official-latest"),
    LotteryGame("lotto-649", "大樂透", "台灣", 6, 1, 49, True, "官方最新開獎已接通，歷史資料驗證中", "official-latest"),
    LotteryGame("daily-3", "三星彩", "台灣", 3, 0, 9, False, "官方最新開獎已接通，歷史資料驗證中", "official-latest"),
    LotteryGame("daily-4", "四星彩", "台灣", 4, 0, 9, False, "官方最新開獎已接通，歷史資料驗證中", "official-latest"),
)

BY_CODE = {game.code: game for game in GAMES}


def catalog_rows() -> tuple[tuple[str, str, str], ...]:
    """LINE-safe display rows; status is intentionally not inferred from data."""
    return tuple((game.name, game.code, game.status) for game in GAMES)


def validate_main_numbers(game_code: str, numbers: list[int]) -> bool:
    """Validate a main draw before it can be presented as verified in LINE."""
    game = BY_CODE[game_code]
    return (
        len(numbers) == game.draw_size
        and (not game.distinct_numbers or len(set(numbers)) == game.draw_size)
        and all(game.min_number <= number <= game.max_number for number in numbers)
    )
