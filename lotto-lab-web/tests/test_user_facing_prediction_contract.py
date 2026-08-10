import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "public"
CONTRACT = json.loads((PUBLIC / "user_facing_prediction_contract.json").read_text(encoding="utf-8"))
PRODUCT = (PUBLIC / "product.js").read_text(encoding="utf-8")
HTML = (PUBLIC / "index.html").read_text(encoding="utf-8")


def test_user_prediction_top5_count_5():
    assert CONTRACT["tw539"]["top5_count"] == 5


def test_user_prediction_top10_count_10():
    assert CONTRACT["tw539"]["top10_count"] == 10


def test_user_prediction_top15_count_15():
    assert CONTRACT["tw539"]["top15_count"] == 15


def test_prediction_sets_nested():
    assert CONTRACT["tw539"]["sets_nested"] is True


def test_prediction_preserves_model_order():
    assert CONTRACT["tw539"]["preserve_model_order"] is True
    assert "snapshot?.reasons" in PRODUCT
    assert "sort((a, b) => Number(a.rank) - Number(b.rank))" in PRODUCT


def test_no_fake_numeric_full_ranking():
    assert CONTRACT["tw539"]["full_ranking_requires_ranked_39"] is True
    assert "完整號碼排名" not in HTML
    assert "正式 Prediction 前 15 名" in HTML


def test_zero_sample_has_no_fake_metrics():
    assert CONTRACT["zero_sample_metrics"] == "HIDDEN_AS_DASH"
    assert "尚無可用樣本" in PRODUCT
    assert "平均與命中率 —" in PRODUCT


def test_backtest_legacy_groups_5():
    legacy = json.loads((PUBLIC / "legacy_product_contract.json").read_text(encoding="utf-8"))
    assert len(legacy["backtest_legacy_groups"]) == 5
    assert "五組正式模型驗證" in PRODUCT


def test_baselines_not_counted_as_models():
    assert CONTRACT["baselines_are_models"] is False
    assert "只作研究控制" in PRODUCT


def test_fantasy5_paused_not_shown_as_formal_recommendation():
    assert CONTRACT["fantasy5_formal_recommendation"] == "PAUSED"
    assert "正式推薦暫停" in HTML


def test_auto_match_uses_original_prediction_hash():
    assert "Prediction Hash" in HTML
    assert "record.predictionHash || record.snapshotHash" in PRODUCT
