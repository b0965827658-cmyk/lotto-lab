import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
CONTRACT = json.loads((PUBLIC / "legacy_product_contract.json").read_text(encoding="utf-8"))
PRODUCT_JS = (PUBLIC / "product.js").read_text(encoding="utf-8")
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")
LEGACY = (PUBLIC / "legacy-tools.html").read_text(encoding="utf-8")


def test_tw539_hot_cold_has_39_numbers():
    assert CONTRACT["tw539_number_universe"] == 39
    assert "Array.from({ length: 39 }" in PRODUCT_JS
    assert "data-stat-number" in PRODUCT_JS
    assert "stats.all.map" in PRODUCT_JS


def test_backtest_has_all_5_legacy_groups():
    assert len(CONTRACT["backtest_legacy_groups"]) == 5
    assert "多模型集成（整體）" in PRODUCT_JS
    for label in CONTRACT["backtest_legacy_groups"][1:]:
        assert label in (ROOT / "data" / "tw539_model_store.json").read_text(encoding="utf-8")


def test_legacy_routes_present():
    assert (PUBLIC / "legacy-tools.html").exists()
    assert INDEX.count("/legacy-tools.html?game=") == 0
    assert CONTRACT["unified_workspace"]["independent_legacy_product_entry"] is False
    assert "data-game=\"tw539\"" in LEGACY
    assert "data-game=\"ca-fantasy5\"" in LEGACY


def test_legacy_feature_expected_counts():
    assert CONTRACT["legacy_feature_total"] >= 26
    assert CONTRACT["tw539_number_universe"] == 39
    assert len(CONTRACT["backtest_legacy_groups"]) == 5


def test_auto_match_legacy_fields():
    for field in CONTRACT["required_auto_match_fields"]:
        assert field in PRODUCT_JS
    assert "等待開獎；尚未結算，不以 0 代替結果" in PRODUCT_JS
    assert "matched_numbers" in PRODUCT_JS


def test_history_legacy_fields():
    for field in CONTRACT["required_history_fields"]:
        assert field in PRODUCT_JS
    assert "data-history-row" in PRODUCT_JS
    assert "完整顯示" in PRODUCT_JS


def test_no_frontend_data_truncation():
    assert not re.search(r"history[^\n]{0,240}slice\(0,\s*30\)", PRODUCT_JS, re.I)
    assert "rows.slice(0, 30).map" not in PRODUCT_JS
    assert "original.filter" in PRODUCT_JS and "renderHistoryRows" in PRODUCT_JS


def test_disclaimer_is_not_reduced_to_one_sentence():
    for panel in ("tw-research", "f5-research"):
        start = INDEX.index(f'data-feature-panel="{panel}"')
        fragment = INDEX[start:start + 1200]
        assert fragment.count("<p>") >= 3
        assert "不保證中獎" in fragment
