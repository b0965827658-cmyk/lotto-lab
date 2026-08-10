import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
INDEX = (PUBLIC / "index.html").read_text(encoding="utf-8")
PRODUCT = (PUBLIC / "product.js").read_text(encoding="utf-8")
LIFECYCLE = (PUBLIC / "saved-numbers.js").read_text(encoding="utf-8")
CONTRACT = json.loads((PUBLIC / "legacy_product_contract.json").read_text(encoding="utf-8"))


def test_legacy_features_26_present():
    assert CONTRACT["legacy_feature_total"] == 26
    assert CONTRACT["contract_may_not_be_lowered_by_ui_redesign"] is True


def test_tw539_number_universe_39():
    assert CONTRACT["tw539_number_universe"] == 39
    assert "Array.from({ length: 39 }" in PRODUCT


def test_backtest_groups_5():
    assert len(CONTRACT["backtest_legacy_groups"]) == 5


def test_saved_number_active_to_settled():
    assert "record.status = STATUS.SETTLED" in LIFECYCLE
    assert "matched_numbers" in LIFECYCLE and "hit_count" in LIFECYCLE


def test_saved_number_settled_to_archived():
    assert "record.status === STATUS.SETTLED" in LIFECYCLE
    assert "record.status = STATUS.ARCHIVED" in LIFECYCLE


def test_pending_draw_not_zero_hit():
    assert "hit_count: null" in LIFECYCLE
    assert "尚未結算，不以 0 代替結果" in PRODUCT


def test_saved_number_duplicate_guard():
    assert "reason: 'DUPLICATE'" in LIFECYCLE
    assert "這組號碼已儲存" in PRODUCT


def test_saved_number_history_preserved():
    for field in ("actual_numbers", "matched_numbers", "settled_at", "archived_at", "hidden_at"):
        assert field in LIFECYCLE
    assert "從我的紀錄隱藏" in PRODUCT


def test_legacy_saved_number_migration():
    for key in ("lotto-lab-saved-picks", "star-picks-tw", "star-picks-f5", "LEGACY_UNASSIGNED"):
        assert key in LIFECYCLE


def test_saved_numbers_do_not_enter_research_evidence():
    assert "fetch(" not in LIFECYCLE
    assert "research" not in LIFECYCLE.lower()


def test_mobile_workspace_navigation():
    sections = CONTRACT["unified_workspace"]["workspace_sections"]
    for section in sections:
        assert INDEX.count(f">{section}<") >= 2
    css = (PUBLIC / "product-v2.css").read_text(encoding="utf-8")
    assert ".workspace-nav" in css and "grid-template-columns:repeat(2" in css
    assert "/legacy-tools.html?game=" not in INDEX
