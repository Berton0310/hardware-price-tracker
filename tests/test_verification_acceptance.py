"""驗收自動化測試套件 (tests/test_verification_acceptance.py)

驗證 Agent (Verification Agent) 獨立驗收測試：
1. 規格群組抽取與標籤剝離（[賣、Facebook 字樣、RAM 容量 vs 512GB SSD）。
2. 價格抽取準確性（PTT 1~5合售 8000、合買6000、5.3萬、售 15000、底價 12000）與排除 <= 0 貼文。
3. 原價屋配件過濾（mini-DP $190、支架、排線過濾）。
4. 前端下拉選單（新品+二手聯集熱門型號）與無新品指標卡優雅展示。
5. 實體資料庫 data/hardware.db 之乾淨度（無 '[' 開頭、無 'Facebook'、無 $190 顯卡配件）。
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from analysis.price_analysis import (
    calculate_spec_group_discount,
    compare_platforms_and_sellers,
    evaluate_listing_price,
)
from app.main_app import format_metric_card_data, get_spec_groups
from cleaners.llm_parser import fallback_regex_parse
from crawlers.coolpc_crawler import parse_coolpc_html
from database.db_manager import init_db, record_new_price, upsert_product, upsert_used_listing


class TestSpecGroupAndTagStripping:
    """驗證規格群組抽取與標籤剝離。"""

    def test_strip_brackets_tag_gpu(self):
        """驗證 '[賣/雙北/面交] 技嘉 RTX 4070 SUPER' 抽取之 spec_group 必為 'RTX 4070 SUPER'，絕非 '[賣'。"""
        title = "[賣/雙北/面交] 技嘉 RTX 4070 SUPER"
        content = "如題，少用如新，欲售價格 18500"
        res = fallback_regex_parse(title, content)
        assert res["spec_group"] == "RTX 4070 SUPER"
        assert not res["spec_group"].startswith("[")
        assert "賣" not in res["spec_group"]

    def test_no_facebook_spec_group(self):
        """驗證包含 Facebook 介面字樣之文字絕不可產生 'Facebook' 之 spec_group。"""
        title = "Facebook 贊助賣場 技嘉 RTX 4070 SUPER 顯卡自售"
        content = "來自 FB 社團轉發，售 19000，台北可面交"
        res = fallback_regex_parse(title, content)
        assert res["spec_group"] != "Facebook"
        assert res["spec_group"] != "FB"
        assert res["spec_group"] == "RTX 4070 SUPER"

        # 極端情境：無具體顯卡型號，只有 FB 字樣時的 fallback 也不可為 Facebook
        title2 = "【Facebook 二手硬體交流】便宜售出"
        content2 = "售價 5000"
        res2 = fallback_regex_parse(title2, content2)
        assert res2["spec_group"].lower() != "facebook"
        assert not res2["spec_group"].startswith("[")

    def test_ram_not_misidentified_by_512gb_ssd(self):
        """驗證 RAM 記憶體容量不可將 512GB SSD 誤判為 'DDR4-3200 512G'。"""
        # 情境一：貼文內含 DDR4-3200 與 512GB SSD (例如升級換下)
        title = "[賣/台中/面交] 金士頓 DDR4-3200 16G 記憶體 附贈 512GB SSD"
        content = "記憶體金士頓 DDR4-3200 16G，另外送一顆 512GB SSD，欲售價格 1200"
        res = fallback_regex_parse(title, content)
        assert res["category"] == "RAM"
        assert res["spec_group"] != "DDR4-3200 512G"
        assert res["spec_group"] == "DDR4-3200 16G"

        # 情境二：純 512GB SSD 貼文
        title_ssd = "[賣/全省/店到店] 美光 512GB SSD 固態硬碟"
        content_ssd = "◎欲售價格：800"
        res_ssd = fallback_regex_parse(title_ssd, content_ssd)
        assert res_ssd["category"] == "SSD"
        assert "512GB" in res_ssd["spec_group"] or "512G" in res_ssd["spec_group"]
        assert "DDR" not in res_ssd["spec_group"]


class TestPriceExtractionAccuracy:
    """驗證價格抽取準確性與過濾無效價格。"""

    def test_various_price_formats(self):
        """驗證 PTT「1~5合售 8000」、「合買6000」、「5.3萬」、「售 15000」、「底價 12000」皆大於 0 且準確。"""
        # 1. 1~5合售 8000
        content_bundle_range = """
        ◎硬體型號：ASUS RTX 4060
        ◎欲售價格：1~5合售 8000
        """
        r1 = fallback_regex_parse("[賣/雙北/面交] 華碩 RTX 4060", content_bundle_range)
        assert r1["used_asking_price"] == 8000

        # 2. 合買6000
        content_bundle = """
        ◎硬體型號：AMD R7 5700X
        ◎欲售價格：合買6000
        """
        r2 = fallback_regex_parse("[賣/全國] AMD CPU", content_bundle)
        assert r2["used_asking_price"] == 6000

        # 3. 5.3萬
        content_wan = """
        ◎硬體型號：微星 RTX 4090 SUPRIM
        ◎欲售價格：5.3萬
        """
        r3 = fallback_regex_parse("[賣/台北] 微星 4090", content_wan)
        assert r3["used_asking_price"] == 53000

        # 4. 售 15000 (常見於 FB / PTT 簡短格式)
        title_sell = "微星 RTX 4070 售 15000 面交"
        content_sell = "保固內良品，售 15000 台北可自取"
        r4 = fallback_regex_parse(title_sell, content_sell)
        assert r4["used_asking_price"] == 15000

        # 5. 底價 12000
        content_floor = """
        ◎硬體型號：技嘉 RTX 4060 Ti 8G
        ◎欲售價格：原價 14900，底價 12000 不議價
        """
        r5 = fallback_regex_parse("[賣/新竹] 技嘉 4060 Ti", content_floor)
        assert r5["used_asking_price"] == 12000

    def test_price_analysis_excludes_zero_or_negative_prices(self):
        """驗證 price_analysis 中的中位數、折數計算皆徹底排除 used_asking_price <= 0 的貼文。"""
        conn = init_db(":memory:")
        # 插入新品
        pid = upsert_product(conn, "GPU", "RTX 4070", "Gigabyte", "RTX 4070 12G", 3)
        record_new_price(conn, pid, "2026-03-01", 20000, 18000, "不變", True)

        # 插入二手貼文：一筆正常 14000，一筆 0 元 (或未標價)，一筆 -500 (異常)
        upsert_used_listing(
            conn,
            {
                "post_id": "p1",
                "product_id": pid,
                "spec_group": "RTX 4070",
                "source_platform": "PTT",
                "seller_name": "seller1",
                "seller_type": "個人自售",
                "used_asking_price": 14000,
                "warranty_status": "原廠保內",
                "trade_method": "面交",
                "comment_count": 0,
                "sale_status": "刊登中",
                "post_time": "2026-03-02 10:00:00",
                "post_url": "https://ptt.cc/1",
            },
        )
        upsert_used_listing(
            conn,
            {
                "post_id": "p2",
                "product_id": pid,
                "spec_group": "RTX 4070",
                "source_platform": "PTT",
                "seller_name": "seller2",
                "seller_type": "個人自售",
                "used_asking_price": 0,
                "warranty_status": "過保",
                "trade_method": "面交",
                "comment_count": 0,
                "sale_status": "刊登中",
                "post_time": "2026-03-02 11:00:00",
                "post_url": "https://ptt.cc/2",
            },
        )
        upsert_used_listing(
            conn,
            {
                "post_id": "p3",
                "product_id": pid,
                "spec_group": "RTX 4070",
                "source_platform": "PTT",
                "seller_name": "seller3",
                "seller_type": "個人自售",
                "used_asking_price": -500,
                "warranty_status": "過保",
                "trade_method": "面交",
                "comment_count": 0,
                "sale_status": "刊登中",
                "post_time": "2026-03-02 12:00:00",
                "post_url": "https://ptt.cc/3",
            },
        )

        # 1. 規格群組折數計算
        res_discount = calculate_spec_group_discount(conn, "RTX 4070", days=30)
        assert res_discount["used_listing_count"] == 1
        assert res_discount["used_median_price"] == 14000.0
        assert res_discount["used_min_price"] == 14000
        # 14000 / 18000 = 77.8% (7.8折)
        assert res_discount["discount_ratio"] == 77.8
        assert res_discount["saved_amount"] == 4000.0

        # 2. 平台比較也需排除 <= 0
        res_comp = compare_platforms_and_sellers(conn, "RTX 4070")
        assert res_comp["PTT_individual"]["count"] == 1
        assert res_comp["PTT_individual"]["median_price"] == 14000.0

        # 3. 開價評估也需排除 <= 0
        res_eval = evaluate_listing_price(conn, "RTX 4070", 13000)
        assert res_eval["benchmark_price"] == 14000.0


class TestCoolpcAccessoryFilter:
    """驗證原價屋 GPU 爬蟲配件過濾。"""

    def test_filter_gpu_accessories(self):
        """驗證 GPU 爬蟲過濾 mini-DP 轉接線 ($190)、支架、排線等雜項，確保新品顯卡最低價絕非 $190。"""
        html = """
        <select name="n12">
            <option value="0">--- 顯示卡 ---</option>
            <optgroup label="NVIDIA RTX4060-8GB">
                <option value="1">mini-DP 轉接線/DP轉接頭/公轉母, $190 ◆ ★</option>
                <option value="2">顯卡支撐架/ARGB金屬支架, $399 ◆ ★</option>
                <option value="3">PCIe 4.0 顯卡延長線/排線 20cm, $450 ◆ ★</option>
                <option value="4">華碩 DUAL-RTX4060-O8G-EVO, $9990 ◆ ★</option>
                <option value="5">微星 RTX4060 VENTUS 2X 8G OC, $9490 ◆ ★</option>
            </optgroup>
        </select>
        """
        items = parse_coolpc_html(html)
        assert len(items) == 2
        prices = [it["current_price"] for it in items]
        assert 190 not in prices
        assert 399 not in prices
        assert 450 not in prices
        assert min(prices) == 9490
        assert all(it["category"] == "GPU" for it in items)
        for it in items:
            assert "轉接" not in it["model_name"]
            assert "支架" not in it["model_name"]
            assert "排線" not in it["model_name"]
            assert it["current_price"] >= 500


class TestFrontendDropdownAndMetricCards:
    """驗證前端下拉選單與無新品指標卡。"""

    def test_get_spec_groups_includes_both_new_and_used(self):
        """驗證 get_spec_groups(conn, 'GPU') 能同時涵蓋 products 與 used_market_listings 中的二手熱門型號。"""
        conn = init_db(":memory:")
        # products 內有 RTX 4070 SUPER
        upsert_product(conn, "GPU", "RTX 4070 SUPER", "Gigabyte", "RTX 4070 SUPER Gaming", 3)

        # used_market_listings 內有已在原價屋停售的二手熱門卡：RTX 3070、RTX 3060 Ti
        upsert_used_listing(
            conn,
            {
                "post_id": "u1",
                "product_id": None,
                "spec_group": "RTX 3070",
                "source_platform": "PTT",
                "seller_name": "seller1",
                "seller_type": "個人自售",
                "used_asking_price": 8000,
                "warranty_status": "過保",
                "trade_method": "面交",
                "comment_count": 1,
                "sale_status": "刊登中",
                "post_time": "2026-03-01 12:00:00",
                "post_url": "https://ptt.cc/3070",
            },
        )
        upsert_used_listing(
            conn,
            {
                "post_id": "u2",
                "product_id": None,
                "spec_group": "RTX 3060 Ti",
                "source_platform": "FB",
                "seller_name": "seller2",
                "seller_type": "個人自售",
                "used_asking_price": 6500,
                "warranty_status": "過保",
                "trade_method": "面交",
                "comment_count": 2,
                "sale_status": "刊登中",
                "post_time": "2026-03-01 13:00:00",
                "post_url": "https://fb.com/3060ti",
            },
        )

        groups = get_spec_groups(conn, "GPU")
        assert "RTX 4070 SUPER" in groups
        assert "RTX 3070" in groups
        assert "RTX 3060 Ti" in groups

    def test_format_metric_card_data_no_new_price(self):
        """驗證在新品無報價（None 或 0）時，format_metric_card_data 能優雅呈現「原價屋目前無新品在售」，不顯示 0% 或破版折數。"""
        discount_info = {
            "spec_group": "RTX 3070",
            "new_min_price": None,  # 無新品報價
            "used_median_price": 8200.0,
            "used_min_price": 7800,
            "discount_ratio": None,  # 無折數
            "saved_amount": None,    # 無價差
            "seller_type_used": "個人自售",
            "used_listing_count": 8,
        }
        trend_info = {
            "product_id": 999,
            "current_price": None,
            "price_percentile": None,
            "discount_authenticity": "無歷史資料",
        }

        cards = format_metric_card_data(trend_info, discount_info)

        # Card 1: 新品
        assert cards["card1_val"] == "原價屋目前無新品在售"
        assert "已停售" in cards["card1_delta"] or "二手特規" in cards["card1_delta"]

        # Card 2: 二手
        assert "$8,200" in cards["card2_val"]
        assert "最低 $7,800" in cards["card2_delta"]

        # Card 3: 二手折數（絕不能是 0% 或 0 折）
        assert cards["card3_val"] == "無折數數據"
        assert "無法比價" in cards["card3_delta"]

        # Card 4: 節省金額
        assert cards["card4_val"] == "無價差數據"
        assert "已停售" in cards["card4_delta"]


class TestRealDatabaseIntegrity:
    """驗證 data/hardware.db 實體資料庫狀態。"""

    def test_real_database_records(self):
        """查詢資料庫，斷言 spec_group 以 [ 開頭筆數為 0，spec_group = 'Facebook' 筆數為 0，且無 $190 顯卡配件。"""
        db_path = Path("data/hardware.db")
        assert db_path.exists(), f"實體資料庫檔案 {db_path} 不存在！"

        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()

        # 1. 斷言 used_market_listings 中 spec_group 以 [ 開頭的筆數為 0
        c.execute("SELECT COUNT(*), GROUP_CONCAT(spec_group) FROM used_market_listings WHERE spec_group LIKE '[%';")
        row = c.fetchone()
        count_bracket_used = row[0]
        assert count_bracket_used == 0, f"used_market_listings 存在 {count_bracket_used} 筆以 '[' 開頭的 spec_group: {row[1]}"

        # 2. 斷言 products 中 spec_group 以 [ 開頭的筆數為 0
        c.execute("SELECT COUNT(*) FROM products WHERE spec_group LIKE '[%';")
        count_bracket_prod = c.fetchone()[0]
        assert count_bracket_prod == 0, f"products 存在 {count_bracket_prod} 筆以 '[' 開頭的 spec_group"

        # 3. 斷言 spec_group = 'Facebook' 筆數為 0
        c.execute("SELECT COUNT(*) FROM used_market_listings WHERE LOWER(TRIM(spec_group)) = 'facebook';")
        count_fb_used = c.fetchone()[0]
        assert count_fb_used == 0, f"used_market_listings 存在 {count_fb_used} 筆 spec_group = 'Facebook'"

        c.execute("SELECT COUNT(*) FROM products WHERE LOWER(TRIM(spec_group)) = 'facebook';")
        count_fb_prod = c.fetchone()[0]
        assert count_fb_prod == 0, f"products 存在 {count_fb_prod} 筆 spec_group = 'Facebook'"

        # 4. 斷言 GPU 產品無 $190 顯卡配件或低於 500 的配件
        c.execute("""
            SELECT COUNT(*), GROUP_CONCAT(p.model_name || ':' || h.current_new_price)
            FROM products p
            JOIN new_price_history h ON p.product_id = h.product_id
            WHERE p.category = 'GPU' AND (h.current_new_price = 190 OR h.current_new_price < 500);
        """)
        row_cheap = c.fetchone()
        cheap_gpu_count = row_cheap[0]
        assert cheap_gpu_count == 0, f"新品顯卡中存在 {cheap_gpu_count} 筆小於 $500 或等於 $190 的配件: {row_cheap[1]}"

        conn.close()
