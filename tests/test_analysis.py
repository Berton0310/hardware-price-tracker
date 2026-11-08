"""單元測試：M3 價格分析與演算法模組驗收 (tests/test_analysis.py)

驗證 analysis/price_analysis.py 之 4 大核心分析函數：
1. analyze_new_price_trend (歷史走勢、百分位數、真假降價、極端邊界防護)
2. calculate_spec_group_discount (新品最低 vs 二手自售中位數、折數、節省金額、缺失值防護)
3. compare_platforms_and_sellers (4 組維度統計、工作室溢價率正負比、跨平台比對)
4. evaluate_listing_price (四級行情判定、已售出優先、全體二手退回、天花板新品防偏貴、差價與幅度計算)
"""

import datetime
import sqlite3
import pytest

from database.db_manager import (
    init_db,
    upsert_product,
    record_new_price,
    upsert_used_listing,
)
from analysis.price_analysis import (
    analyze_new_price_trend,
    calculate_spec_group_discount,
    compare_platforms_and_sellers,
    evaluate_listing_price,
)


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """提供乾淨的 In-Memory SQLite 連線。"""
    conn = init_db(":memory:")
    yield conn
    conn.close()


class TestAnalyzeNewPriceTrend:
    """測試 2.1 歷史價格走勢與百分位數 (analyze_new_price_trend)"""

    def test_empty_database_returns_safe_defaults(self, mem_db: sqlite3.Connection):
        """空資料庫或無歷史資料時，應回傳預設結構而不噴例外。"""
        res = analyze_new_price_trend(mem_db, product_id=999, days=90)
        assert res["product_id"] == 999
        assert res["max_price"] is None
        assert res["min_price"] is None
        assert res["avg_price"] is None
        assert res["current_price"] is None
        assert res["price_percentile"] is None
        assert res["discount_authenticity"] == "無歷史資料"
        assert res["price_history"] == []

    def test_single_record_percentile_zero_division_guard(self, mem_db: sqlite3.Connection):
        """單筆資料（最高價 == 最低價）時，百分位數應安全歸為 50.0，杜絕除以零。"""
        pid = upsert_product(mem_db, "GPU", "RTX 4060", "ASUS", "ASUS-4060-DUAL")
        record_new_price(mem_db, pid, "2026-11-01", 10000, 10000, "不變")

        res = analyze_new_price_trend(mem_db, pid, days=0)
        assert res["max_price"] == 10000
        assert res["min_price"] == 10000
        assert res["avg_price"] == 10000.0
        assert res["current_price"] == 10000
        assert res["price_percentile"] == 50.0
        assert res["discount_authenticity"] == "正常報價"

    def test_multiple_records_percentile_calculation(self, mem_db: sqlite3.Connection):
        """多筆價格紀錄時，準確計算高、低、均價與當前位階百分位數。"""
        pid = upsert_product(mem_db, "GPU", "RTX 4060", "ASUS", "ASUS-4060-DUAL")
        today = datetime.date.today()
        d1 = (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        d2 = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        d3 = today.strftime("%Y-%m-%d")

        record_new_price(mem_db, pid, d1, 10000, 10000, "不變")
        record_new_price(mem_db, pid, d2, 20000, 20000, "↗漲價")
        record_new_price(mem_db, pid, d3, 20000, 15000, "不變")

        res = analyze_new_price_trend(mem_db, pid, days=30)
        assert res["min_price"] == 10000
        assert res["max_price"] == 20000
        assert res["avg_price"] == 15000.0
        assert res["current_price"] == 15000
        assert res["price_percentile"] == 50.0

    def test_fluctuation_percentile_accuracy(self, mem_db: sqlite3.Connection):
        """驗證價格波動序列 (10000 -> 12000 -> 15000) 各種歷史位階百分位之精確度。"""
        today = datetime.date.today()
        d1 = (today - datetime.timedelta(days=15)).strftime("%Y-%m-%d")
        d2 = (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        d3 = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")

        # 產品 A: 10000 -> 12000 -> 15000 (現價 15000 為歷史高點 => 100.0%)
        p_a = upsert_product(mem_db, "GPU", "SPEC_A", "BRAND", "MODEL_A")
        record_new_price(mem_db, p_a, d1, 10000, 10000)
        record_new_price(mem_db, p_a, d2, 12000, 12000)
        record_new_price(mem_db, p_a, d3, 15000, 15000)
        res_a = analyze_new_price_trend(mem_db, p_a, days=30)
        assert res_a["price_percentile"] == 100.0
        assert res_a["max_price"] == 15000
        assert res_a["min_price"] == 10000

        # 產品 B: 10000 -> 15000 -> 12000 (現價 12000 => (12000-10000)/(15000-10000)*100 = 40.0%)
        p_b = upsert_product(mem_db, "GPU", "SPEC_B", "BRAND", "MODEL_B")
        record_new_price(mem_db, p_b, d1, 10000, 10000)
        record_new_price(mem_db, p_b, d2, 15000, 15000)
        record_new_price(mem_db, p_b, d3, 15000, 12000)
        res_b = analyze_new_price_trend(mem_db, p_b, days=30)
        assert res_b["price_percentile"] == 40.0

        # 產品 C: 15000 -> 12000 -> 10000 (現價 10000 為歷史低點 => 0.0%)
        p_c = upsert_product(mem_db, "GPU", "SPEC_C", "BRAND", "MODEL_C")
        record_new_price(mem_db, p_c, d1, 15000, 15000)
        record_new_price(mem_db, p_c, d2, 15000, 12000)
        record_new_price(mem_db, p_c, d3, 15000, 10000)
        res_c = analyze_new_price_trend(mem_db, p_c, days=30)
        assert res_c["price_percentile"] == 0.0

    def test_genuine_discount_detection(self, mem_db: sqlite3.Connection):
        """真降價：標註 ↘ 且最新價格低於近 30 日平均價。"""
        pid = upsert_product(mem_db, "GPU", "RTX 4060", "MSI", "MSI-4060-VENTUS")
        today = datetime.date.today()
        d1 = (today - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        d2 = today.strftime("%Y-%m-%d")

        # 5 天前 10000，今天降至 8500 標註 ↘降價 (均價 9250，8500 < 9250)
        record_new_price(mem_db, pid, d1, 10000, 10000, "不變")
        record_new_price(mem_db, pid, d2, 10000, 8500, "↘降價")

        res = analyze_new_price_trend(mem_db, pid, days=30)
        assert res["current_price"] == 8500
        assert res["discount_authenticity"] == "創近期新低 (真降價)"

    def test_fake_discount_detection(self, mem_db: sqlite3.Connection):
        """假降價：先大漲再標註 ↘ 降價，但價格仍高於/等於近 30 日平均價。"""
        pid = upsert_product(mem_db, "GPU", "RTX 4060", "MSI", "MSI-4060-VENTUS")
        today = datetime.date.today()
        d1 = (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        d2 = (today - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
        d3 = today.strftime("%Y-%m-%d")

        # 原價 10000，先暴漲到 15000，再假降價至 13000 (30日均價為 (10000+15000+13000)/3 = 12666.7, 13000 >= 12666.7)
        record_new_price(mem_db, pid, d1, 10000, 10000, "不變")
        record_new_price(mem_db, pid, d2, 15000, 15000, "↗漲價")
        record_new_price(mem_db, pid, d3, 15000, 13000, "↘限時下殺")

        res = analyze_new_price_trend(mem_db, pid, days=30)
        assert res["current_price"] == 13000
        assert res["discount_authenticity"] == "先漲後降 (假降價)"

    def test_no_change_or_price_hike_authenticity(self, mem_db: sqlite3.Connection):
        """無變動或漲價標籤時，價格真實性應標註為『正常報價』。"""
        pid = upsert_product(mem_db, "GPU", "RTX 4060", "ASUS", "ASUS-NORMAL")
        today = datetime.date.today().strftime("%Y-%m-%d")
        record_new_price(mem_db, pid, today, 10000, 11000, "↗漲價")

        res = analyze_new_price_trend(mem_db, pid, days=30)
        assert res["discount_authenticity"] == "正常報價"


class TestCalculateSpecGroupDiscount:
    """測試 2.2 新品 vs. 二手價差與折數 (calculate_spec_group_discount)"""

    def test_standard_spec_discount_with_individual_seller(self, mem_db: sqlite3.Connection):
        """標準情境：準確比對新品最低價與個人自售中位數，計算折數與節省金額。"""
        spec = "RTX 4070 12G"
        p1 = upsert_product(mem_db, "GPU", spec, "ASUS", "ASUS-4070-1")
        p2 = upsert_product(mem_db, "GPU", spec, "MSI", "MSI-4070-2")
        today = datetime.date.today().strftime("%Y-%m-%d")
        record_new_price(mem_db, p1, today, 20000, 20000)
        record_new_price(mem_db, p2, today, 18000, 18000)

        posts = [
            {"post_id": "U1", "spec_group": spec, "source_platform": "PTT", "seller_name": "u1", "seller_type": "個人自售", "used_asking_price": 13000, "post_time": f"{today} 10:00:00"},
            {"post_id": "U2", "spec_group": spec, "source_platform": "PTT", "seller_name": "u2", "seller_type": "個人自售", "used_asking_price": 14000, "post_time": f"{today} 11:00:00"},
            {"post_id": "U3", "spec_group": spec, "source_platform": "PTT", "seller_name": "u3", "seller_type": "個人自售", "used_asking_price": 15000, "post_time": f"{today} 12:00:00"},
            {"post_id": "U4", "spec_group": spec, "source_platform": "FB_Group", "seller_name": "u4", "seller_type": "工作室", "used_asking_price": 16000, "post_time": f"{today} 13:00:00"},
        ]
        for p in posts:
            upsert_used_listing(mem_db, p)

        res = calculate_spec_group_discount(mem_db, spec, days=30)
        assert res["new_min_price"] == 18000
        assert res["used_median_price"] == 14000.0
        assert res["seller_type_used"] == "個人自售"
        assert res["discount_ratio"] == 77.8
        assert res["saved_amount"] == 4000.0

    def test_fallback_to_all_used_when_no_individual(self, mem_db: sqlite3.Connection):
        """若無個人自售貼文，應自動退回全體二手貼文中位數。"""
        spec = "RTX 4080 16G"
        p = upsert_product(mem_db, "GPU", spec, "ASUS", "ASUS-4080")
        today = datetime.date.today().strftime("%Y-%m-%d")
        record_new_price(mem_db, p, today, 36000, 36000)

        post = {
            "post_id": "W1", "spec_group": spec, "source_platform": "FB_Group",
            "seller_name": "studio", "seller_type": "工作室", "used_asking_price": 30000,
            "post_time": f"{today} 12:00:00"
        }
        upsert_used_listing(mem_db, post)

        res = calculate_spec_group_discount(mem_db, spec, days=30)
        assert res["new_min_price"] == 36000
        assert res["used_median_price"] == 30000.0
        assert res["seller_type_used"] == "全體二手"
        assert res["discount_ratio"] == round((30000 / 36000) * 100.0, 1)

    def test_missing_data_returns_none(self, mem_db: sqlite3.Connection):
        """兩者皆缺時，比值與金額安全返回 None。"""
        res = calculate_spec_group_discount(mem_db, "NON_EXIST_SPEC", days=30)
        assert res["new_min_price"] is None
        assert res["used_median_price"] is None
        assert res["discount_ratio"] is None
        assert res["saved_amount"] is None

    def test_partial_missing_data_and_zero_division_guard(self, mem_db: sqlite3.Connection):
        """單邊資料缺失或新品售價為 0 時的防護斷言。"""
        today = datetime.date.today().strftime("%Y-%m-%d")

        # 情境 A: 僅有新品，無任何二手資料
        spec_a = "SPEC_NEW_ONLY"
        pa = upsert_product(mem_db, "GPU", spec_a, "BRAND", "MODEL_NEW_ONLY")
        record_new_price(mem_db, pa, today, 10000, 10000)
        res_a = calculate_spec_group_discount(mem_db, spec_a, days=30)
        assert res_a["new_min_price"] == 10000
        assert res_a["used_median_price"] is None
        assert res_a["discount_ratio"] is None
        assert res_a["saved_amount"] is None

        # 情境 B: 僅有二手資料，無任何新品售價
        spec_b = "SPEC_USED_ONLY"
        upsert_used_listing(mem_db, {
            "post_id": "UO1", "spec_group": spec_b, "source_platform": "PTT",
            "seller_name": "u", "seller_type": "個人自售", "used_asking_price": 6000,
            "post_time": f"{today} 10:00:00"
        })
        res_b = calculate_spec_group_discount(mem_db, spec_b, days=30)
        assert res_b["new_min_price"] is None
        assert res_b["used_median_price"] == 6000.0
        assert res_b["discount_ratio"] is None
        assert res_b["saved_amount"] is None

        # 情境 C: 新品價格為 0 (贈品或缺貨標示 0 元防除以零)
        spec_c = "SPEC_ZERO_PRICE"
        pc = upsert_product(mem_db, "GPU", spec_c, "BRAND", "MODEL_ZERO")
        record_new_price(mem_db, pc, today, 0, 0)
        upsert_used_listing(mem_db, {
            "post_id": "UZ1", "spec_group": spec_c, "source_platform": "PTT",
            "seller_name": "u", "seller_type": "個人自售", "used_asking_price": 5000,
            "post_time": f"{today} 10:00:00"
        })
        res_c = calculate_spec_group_discount(mem_db, spec_c, days=30)
        assert res_c["new_min_price"] == 0
        assert res_c["discount_ratio"] is None


class TestComparePlatformsAndSellers:
    """測試 2.3 跨平台與賣家類型比較 (compare_platforms_and_sellers)"""

    def test_four_groups_and_premium_rate(self, mem_db: sqlite3.Connection):
        """驗證 FB 與 PTT 之自售與工作室 4 組統計量及工作室溢價率。"""
        spec = "DDR5 6000 32G"
        today = datetime.date.today().strftime("%Y-%m-%d")
        posts = [
            {"post_id": "P_FB_IND", "spec_group": spec, "source_platform": "FB_Group", "seller_name": "s1", "seller_type": "個人自售", "used_asking_price": 2800, "post_time": f"{today} 10:00:00"},
            {"post_id": "P_FB_WS", "spec_group": spec, "source_platform": "FB_Group", "seller_name": "w1", "seller_type": "工作室", "used_asking_price": 3300, "post_time": f"{today} 11:00:00"},
            {"post_id": "P_PTT_IND", "spec_group": spec, "source_platform": "PTT", "seller_name": "s2", "seller_type": "個人自售", "used_asking_price": 3000, "post_time": f"{today} 12:00:00"},
            {"post_id": "P_PTT_WS", "spec_group": spec, "source_platform": "PTT", "seller_name": "w2", "seller_type": "工作室", "used_asking_price": 3500, "post_time": f"{today} 13:00:00"},
        ]
        for p in posts:
            upsert_used_listing(mem_db, p)

        res = compare_platforms_and_sellers(mem_db, spec_group=spec)

        assert res["FB_Group_individual"]["count"] == 1
        assert res["FB_Group_individual"]["mean_price"] == 2800.0

        assert res["FB_Group_workshop"]["count"] == 1
        assert res["FB_Group_workshop"]["mean_price"] == 3300.0

        assert res["PTT_individual"]["count"] == 1
        assert res["PTT_individual"]["mean_price"] == 3000.0

        assert res["PTT_workshop"]["count"] == 1
        assert res["PTT_workshop"]["mean_price"] == 3500.0

        assert res["workshop_premium_rate"] == 17.2
        assert res["fb_workshop_premium_rate"] == round(((3300 - 2800) / 2800) * 100.0, 1)
        assert res["ptt_workshop_premium_rate"] == round(((3500 - 3000) / 3000) * 100.0, 1)

    def test_negative_premium_rate_and_partial_groups(self, mem_db: sqlite3.Connection):
        """工作室價格低於自售（負溢價率）與僅有單一賣家類型時的防除以零檢查。"""
        spec = "SSD 1TB"
        today = datetime.date.today().strftime("%Y-%m-%d")

        # 自售均價 3000，工作室清倉出清 2500 => 溢價率 (2500 - 3000) / 3000 * 100 = -16.7%
        upsert_used_listing(mem_db, {"post_id": "N1", "spec_group": spec, "source_platform": "PTT", "seller_name": "s", "seller_type": "個人自售", "used_asking_price": 3000, "post_time": f"{today} 10:00:00"})
        upsert_used_listing(mem_db, {"post_id": "N2", "spec_group": spec, "source_platform": "PTT", "seller_name": "w", "seller_type": "工作室", "used_asking_price": 2500, "post_time": f"{today} 11:00:00"})

        res = compare_platforms_and_sellers(mem_db, spec_group=spec)
        assert res["workshop_premium_rate"] == -16.7

        # 僅有自售，無任何工作室貼文
        spec_ind_only = "RAM_IND_ONLY"
        upsert_used_listing(mem_db, {"post_id": "N3", "spec_group": spec_ind_only, "source_platform": "PTT", "seller_name": "s", "seller_type": "個人自售", "used_asking_price": 1000, "post_time": f"{today} 10:00:00"})
        res_ind = compare_platforms_and_sellers(mem_db, spec_group=spec_ind_only)
        assert res_ind["workshop_premium_rate"] is None

    def test_empty_platforms_comparison(self, mem_db: sqlite3.Connection):
        """空資料庫時各組 count 均為 0，數值為 None。"""
        res = compare_platforms_and_sellers(mem_db, "EMPTY_SPEC")
        for k in ["FB_Group_individual", "FB_Group_workshop", "PTT_individual", "PTT_workshop"]:
            assert res[k]["count"] == 0
            assert res[k]["median_price"] is None
            assert res[k]["mean_price"] is None
        assert res["workshop_premium_rate"] is None


class TestEvaluateListingPrice:
    """測試 2.4 二手最新貼文行情評估 (evaluate_listing_price)"""

    def test_sold_status_priority_over_unsold(self, mem_db: sqlite3.Connection):
        """優先採用已售出之中位數作為評估基準。"""
        spec = "RTX 4060 8G"
        today = datetime.date.today().strftime("%Y-%m-%d")
        posts = [
            {"post_id": "S1", "spec_group": spec, "source_platform": "PTT", "seller_name": "a", "used_asking_price": 8000, "sale_status": "已售出", "post_time": f"{today} 09:00:00"},
            {"post_id": "S2", "spec_group": spec, "source_platform": "PTT", "seller_name": "b", "used_asking_price": 8200, "sale_status": "已售出", "post_time": f"{today} 10:00:00"},
            {"post_id": "U1", "spec_group": spec, "source_platform": "PTT", "seller_name": "c", "used_asking_price": 9900, "sale_status": "販售中", "post_time": f"{today} 11:00:00"},
        ]
        for p in posts:
            upsert_used_listing(mem_db, p)

        res = evaluate_listing_price(mem_db, spec, asking_price=8100)
        assert res["benchmark_price"] == 8100.0
        assert res["benchmark_source"] == "已售出中位數"
        assert res["evaluation_level"] == "fair"
        assert res["evaluation_label"] == "符合行情"
        assert res["diff_amount"] == 0.0
        assert res["diff_percentage"] == 0.0

    def test_fallback_to_all_used_when_no_sold_posts(self, mem_db: sqlite3.Connection):
        """若無任何『已售出』貼文，自動平滑退回全體二手貼文中位數。"""
        spec = "RTX 4090 24G"
        today = datetime.date.today().strftime("%Y-%m-%d")
        posts = [
            {"post_id": "A1", "spec_group": spec, "source_platform": "PTT", "seller_name": "a", "used_asking_price": 50000, "sale_status": "販售中", "post_time": f"{today} 09:00:00"},
            {"post_id": "A2", "spec_group": spec, "source_platform": "PTT", "seller_name": "b", "used_asking_price": 54000, "sale_status": "議價中", "post_time": f"{today} 10:00:00"},
        ]
        for p in posts:
            upsert_used_listing(mem_db, p)

        res = evaluate_listing_price(mem_db, spec, asking_price=52000)
        assert res["benchmark_price"] == 52000.0
        assert res["benchmark_source"] == "全體二手中位數"
        assert res["evaluation_level"] == "fair"

    def test_four_level_classification_boundary_assertions(self, mem_db: sqlite3.Connection):
        """嚴格驗證四級行情劃分所有邊界數值：
        基準 M = 10000, 新品最低 = 20000 (90%新品為 18000)
        1. asking < 5000 (0.5 M) => 異常低價警示 (防詐騙/故障) (anomaly_low)
        2. 5000 <= asking < 9000 (0.9 M) => 低於行情 (推薦購買) (bargain)
        3. 9000 <= asking <= 11000 (1.1 M) => 符合行情 (fair)
        4. asking > 11000 => 偏貴 (建議買新品) (overpriced)
        """
        spec = "TEST_GPU_10K"
        today = datetime.date.today().strftime("%Y-%m-%d")
        pid = upsert_product(mem_db, "GPU", spec, "BRAND", "MODEL-10K")
        record_new_price(mem_db, pid, today, 20000, 20000)

        upsert_used_listing(mem_db, {
            "post_id": "BM", "spec_group": spec, "source_platform": "PTT",
            "seller_name": "bm", "used_asking_price": 10000, "sale_status": "已售出",
            "post_time": f"{today} 10:00:00"
        })

        # 1. 邊界：4999 (異常低價) vs 5000 (低於行情推薦)
        r_4999 = evaluate_listing_price(mem_db, spec, asking_price=4999)
        assert r_4999["evaluation_level"] == "anomaly_low"
        assert "異常低價" in r_4999["evaluation_label"]

        r_5000 = evaluate_listing_price(mem_db, spec, asking_price=5000)
        assert r_5000["evaluation_level"] == "bargain"
        assert "低於行情" in r_5000["evaluation_label"]

        # 2. 邊界：8999 (低於行情推薦) vs 9000 (符合行情)
        r_8999 = evaluate_listing_price(mem_db, spec, asking_price=8999)
        assert r_8999["evaluation_level"] == "bargain"

        r_9000 = evaluate_listing_price(mem_db, spec, asking_price=9000)
        assert r_9000["evaluation_level"] == "fair"

        # 3. 邊界：11000 (符合行情) vs 11001 (偏貴)
        r_11000 = evaluate_listing_price(mem_db, spec, asking_price=11000)
        assert r_11000["evaluation_level"] == "fair"

        r_11001 = evaluate_listing_price(mem_db, spec, asking_price=11001)
        assert r_11001["evaluation_level"] == "overpriced"
        assert "偏貴" in r_11001["evaluation_label"]

    def test_overpriced_due_to_near_new_ceiling(self, mem_db: sqlite3.Connection):
        """即使在二手中位數 1.1 倍內，但若開價 >= 新品最低價 90%，依然判定為偏貴 (建議買新品)。"""
        spec = "GPU_CHEAP_NEW"
        today = datetime.date.today().strftime("%Y-%m-%d")
        pid = upsert_product(mem_db, "GPU", spec, "BRAND", "MODEL-CHEAP")
        record_new_price(mem_db, pid, today, 11000, 11000)

        upsert_used_listing(mem_db, {
            "post_id": "BM2", "spec_group": spec, "source_platform": "PTT",
            "seller_name": "bm2", "used_asking_price": 10000, "sale_status": "已售出",
            "post_time": f"{today} 10:00:00"
        })

        # 開價 10000 (在二手 1.1 倍內，但 10000 >= 9900 新品 90% 天花板)
        res = evaluate_listing_price(mem_db, spec, asking_price=10000)
        assert res["evaluation_level"] == "overpriced"
        assert "建議買新品" in res["evaluation_label"]

    def test_overpriced_ceiling_overrides_bargain_when_used_median_is_high(
        self, mem_db: sqlite3.Connection
    ):
        """天花板最高防線：當二手市場開價虛高使得二手基準(10000)高於新品(8000)時，
        開價 7500 雖小於二手 0.9M (9000)，但已達新品 9 折 (7200)，必須判定為偏貴買新品而非低於行情。
        """
        spec = "GPU_COLLAPSED_NEW"
        today = datetime.date.today().strftime("%Y-%m-%d")
        pid = upsert_product(mem_db, "GPU", spec, "BRAND", "MODEL-COLLAPSED")
        # 新品下殺至 8000 (90%新品為 7200)
        record_new_price(mem_db, pid, today, 8000, 8000)

        # 二手已售出中位數仍停留在 10000 (0.9M 為 9000)
        upsert_used_listing(mem_db, {
            "post_id": "BM3", "spec_group": spec, "source_platform": "PTT",
            "seller_name": "bm3", "used_asking_price": 10000, "sale_status": "已售出",
            "post_time": f"{today} 10:00:00"
        })

        # 開價 7500: 雖然 < 9000，但 >= 7200 (新品9折)，應建議買新品 (overpriced)
        res = evaluate_listing_price(mem_db, spec, asking_price=7500)
        assert res["evaluation_level"] == "overpriced"
        assert "建議買新品" in res["evaluation_label"]

    def test_no_used_data_fallbacks(self, mem_db: sqlite3.Connection):
        """完全缺乏二手貼文時，平滑支援僅以新品價格作初步評估之降級機制。"""
        spec_no_used = "SPEC_NO_USED_TEST"
        today = datetime.date.today().strftime("%Y-%m-%d")
        pid = upsert_product(mem_db, "GPU", spec_no_used, "BRAND", "MODEL_NO_USED")
        record_new_price(mem_db, pid, today, 10000, 10000)

        # A. 開價 >= 9000 (90% 新品) => 偏貴
        r_over = evaluate_listing_price(mem_db, spec_no_used, asking_price=9500)
        assert r_over["evaluation_level"] == "overpriced"
        assert r_over["benchmark_price"] is None

        # B. 開價 < 4500 (45% 新品) => 異常低價
        r_low = evaluate_listing_price(mem_db, spec_no_used, asking_price=4000)
        assert r_low["evaluation_level"] == "anomaly_low"

        # C. 開價正常範圍 => unknown (無二手行情資料)
        r_norm = evaluate_listing_price(mem_db, spec_no_used, asking_price=7000)
        assert r_norm["evaluation_level"] == "unknown"

        # D. 完全無二手也無新品資料
        r_none = evaluate_listing_price(mem_db, "SPEC_ABSOLUTELY_EMPTY", asking_price=5000)
        assert r_none["evaluation_level"] == "unknown"
        assert r_none["evaluation_label"] == "無行情資料可比對"
