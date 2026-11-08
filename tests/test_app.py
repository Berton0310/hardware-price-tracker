"""Streamlit 儀表板全自動化驗收測試 (tests/test_app.py)

遵循 CLAUDE.md、AGENTS.md 與 docs/modules/04_app.md 規範。
測試範疇：
1. 空資料庫與極端邊界防護 (Graceful Degradation)
2. 頂部 4 大重點指標卡格式化與文字計算正確性
3. Plotly 走勢圖與直條圖結構屬性與參考線斷言
4. 二手比價列表建構、過濾開關與四級狀態徽章
5. Streamlit AppTest 無頭掛載與動態互動無崩潰驗證
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any
from unittest.mock import patch

import pandas as pd
import plotly.graph_objects as go
import pytest
from streamlit.testing.v1 import AppTest

from app.main_app import (
    CATEGORIES,
    DEFAULT_SPEC_GROUPS,
    build_listings_dataframe,
    create_platform_comparison_chart,
    create_price_trend_chart,
    format_metric_card_data,
    get_filtered_listings,
    get_representative_product,
    get_spec_groups,
    get_status_badge,
)
from database.db_manager import (
    init_db,
    record_new_price,
    record_sync_log,
    upsert_product,
    upsert_used_listing,
)


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """提供乾淨的 In-Memory SQLite 資料庫。"""
    return init_db(":memory:")


# ==============================================================================
# 1. 空資料庫與極端邊界防護 (Graceful Degradation)
# ==============================================================================


class TestEmptyDatabaseGracefulDegradation:
    """驗證資料庫為空或無相關資料時，所有查詢與視覺化函式皆能安全運作且不拋出未捕獲異常。"""

    def test_get_spec_groups_empty_db_returns_defaults(self, mem_db: sqlite3.Connection):
        """空資料庫下應安全回傳各類別的預設規格清單。"""
        for cat in CATEGORIES:
            groups = get_spec_groups(mem_db, cat)
            assert isinstance(groups, list)
            assert len(groups) > 0
            assert groups == DEFAULT_SPEC_GROUPS[cat]

    def test_get_spec_groups_unknown_category_and_error_handling(
        self, mem_db: sqlite3.Connection
    ):
        """查詢不存在的類別或連線異常時，應優雅降級回退預設值而不崩潰。"""
        groups = get_spec_groups(mem_db, "UNKNOWN_CATEGORY")
        assert groups == ["RTX 4060 Ti 16G"]

        # 模擬連線關閉時的例外捕獲
        closed_conn = init_db(":memory:")
        closed_conn.close()
        fallback_groups = get_spec_groups(closed_conn, "GPU")
        assert fallback_groups == DEFAULT_SPEC_GROUPS["GPU"]

    def test_get_spec_groups_category_isolation(self, mem_db: sqlite3.Connection):
        """驗證單一大類有資料時，查詢其他大類不會洩漏跨大類規格 (ERR-006 防範)。"""
        # 僅插入 GPU 產品與二手刊登
        p_id = upsert_product(
            mem_db,
            category="GPU",
            spec_group="RTX 4060 Ti 16G",
            brand="ASUS",
            model_name="ASUS Dual RTX 4060 Ti 16G",
        )
        upsert_used_listing(
            mem_db,
            {
                "post_id": "gpu_post_1",
                "product_id": p_id,
                "spec_group": "RTX 4060 Ti 16G",
                "source_platform": "FB_Group",
                "seller_name": "GamerA",
                "seller_type": "個人自售",
                "used_asking_price": 12000,
                "warranty_status": "原廠保內",
                "post_time": "2026-11-01 12:00:00",
            },
        )

        # 查詢 RAM 類別：應回傳 RAM 預設值，絕不可包含 GPU 規格群組
        ram_groups = get_spec_groups(mem_db, "RAM")
        assert "RTX 4060 Ti 16G" not in ram_groups
        assert ram_groups == DEFAULT_SPEC_GROUPS["RAM"]

    def test_get_representative_product_empty_db(self, mem_db: sqlite3.Connection):
        """空資料庫下應回傳 None 而非崩潰。"""
        rep = get_representative_product(mem_db, "RTX 4060 Ti 16G")
        assert rep is None

    def test_get_representative_product_without_price_history(
        self, mem_db: sqlite3.Connection
    ):
        """產品存在但無任何新品歷史價格時，應安全回傳產品資訊且 current_new_price 為 None。"""
        p_id = upsert_product(
            mem_db,
            category="GPU",
            spec_group="RTX 4070 SUPER",
            brand="GIGABYTE",
            model_name="GIGABYTE RTX 4070 SUPER EAGLE",
        )
        rep = get_representative_product(mem_db, "RTX 4070 SUPER")
        assert rep is not None
        assert rep["product_id"] == p_id
        assert rep["model_name"] == "GIGABYTE RTX 4070 SUPER EAGLE"
        assert rep["current_new_price"] is None

    def test_format_metric_card_data_empty(self):
        """全空字典輸入下，指標卡應回傳友善提示字串而非引發 KeyError 或 TypeError。"""
        cards = format_metric_card_data({}, {})
        assert cards["card1_val"] == "無新品報價"
        assert cards["card1_delta"] == "尚無走勢紀錄"
        assert cards["card2_val"] == "無二手數據"
        assert cards["card2_delta"] == "暫無市場刊登"
        assert cards["card3_val"] == "無折數數據"
        assert cards["card3_delta"] == "缺少新品或二手價"
        assert cards["card4_val"] == "無數據"
        assert cards["card4_delta"] == "缺少比價基準"

    def test_create_price_trend_chart_empty(self):
        """歷史紀錄為空時，Plotly 折線圖應安全渲染無資料提示且 Data Traces 為空。"""
        fig = create_price_trend_chart({"price_history": []}, "RTX 4060 Ti 16G")
        assert isinstance(fig, go.Figure)
        assert len(fig.data) == 0
        assert "無歷史紀錄" in fig.layout.title.text
        assert len(fig.layout.annotations) == 1
        assert "目前尚無此規格之新品歷史價格資料" in fig.layout.annotations[0].text

    def test_create_platform_comparison_chart_empty(self):
        """跨平台資訊為空時，Plotly 直條圖應安全渲染無資料提示且 Data Traces 為空。"""
        fig = create_platform_comparison_chart({"groups": {}}, None, "RTX 4060 Ti 16G")
        assert isinstance(fig, go.Figure)
        assert len(fig.data) == 0
        assert "無資料" in fig.layout.title.text
        assert len(fig.layout.annotations) == 1
        assert "目前尚無跨平台與賣家價格比對資料" in fig.layout.annotations[0].text

    def test_build_listings_dataframe_empty(self, mem_db: sqlite3.Connection):
        """貼文清單為空時，應回傳包含 11 個標準欄位之空 DataFrame。"""
        df = build_listings_dataframe([], mem_db, "RTX 4060 Ti 16G", 15000)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
        expected_cols = [
            "平台",
            "發文時間",
            "賣家名稱",
            "賣家類型",
            "型號規格",
            "保固狀態",
            "開價",
            "對比行情價差",
            "新品折數",
            "行情標籤",
            "原文連結",
        ]
        assert list(df.columns) == expected_cols

    def test_get_filtered_listings_empty(self, mem_db: sqlite3.Connection):
        """空資料庫下取得二手刊登應回傳空清單。"""
        listings = get_filtered_listings(mem_db, "RTX 4060 Ti 16G")
        assert listings == []


# ==============================================================================
# 2. 指標卡格式化與計算正確性 (Metric Cards Formatting)
# ==============================================================================


class TestMetricCardFormatting:
    """驗證頂部 4 大重點指標卡之數值計算、折數比率與副標籤格式化。"""

    def test_metric_card_percentiles_tiers(self):
        """驗證歷史位階低檔 (<=25%)、高檔 (>=75%) 與中檔 (26-74%) 格式化。"""
        # 低檔
        low_trend = {
            "current_price": 13990,
            "price_percentile": 15.0,
            "discount_authenticity": "正常報價",
        }
        res_low = format_metric_card_data(low_trend, {"new_min_price": 13990})
        assert res_low["card1_val"] == "$13,990"
        assert res_low["card1_delta"] == "第 15 百分位 (低檔) · 正常報價"

        # 高檔
        high_trend = {
            "current_price": 18500,
            "price_percentile": 85.0,
            "discount_authenticity": "先漲後降假促銷",
        }
        res_high = format_metric_card_data(high_trend, {"new_min_price": 18500})
        assert res_high["card1_val"] == "$18,500"
        assert res_high["card1_delta"] == "第 85 百分位 (高檔) · 先漲後降假促銷"

        # 中檔
        mid_trend = {
            "current_price": 15000,
            "price_percentile": 50.0,
            "discount_authenticity": "真降價",
        }
        res_mid = format_metric_card_data(mid_trend, {"new_min_price": 15000})
        assert res_mid["card1_val"] == "$15,000"
        assert res_mid["card1_delta"] == "第 50 百分位 (中檔) · 真降價"

    def test_metric_card_used_median_and_discount_ratio(self):
        """驗證二手市場中位價、刊登筆數與二手折數計算展示。"""
        discount_info = {
            "new_min_price": 15000,
            "used_median_price": 10875.0,
            "discount_ratio": 72.5,
            "saved_amount": 4125.0,
            "seller_type_used": "個人自售",
            "used_listing_count": 6,
        }
        res = format_metric_card_data({}, discount_info)
        assert res["card2_val"] == "$10,875"
        assert res["card2_delta"] == "個人自售 (6 筆)"
        assert res["card3_val"] == "72.5 折"
        assert res["card3_delta"] == "為新品價之 72.5%"

    def test_metric_card_saved_amount_positive_and_negative(self):
        """驗證可省金額正負值對應之文案（划算 vs 偏貴）。"""
        # 省錢划算
        pos_info = {"saved_amount": 3500.0}
        res_pos = format_metric_card_data({}, pos_info)
        assert res_pos["card4_val"] == "節省 $3,500 元"
        assert res_pos["card4_delta"] == "比買新品划算"

        # 二手開價反比新品偏貴
        neg_info = {"saved_amount": -800.0}
        res_neg = format_metric_card_data({}, neg_info)
        assert res_neg["card4_val"] == "貴 $800 元"
        assert res_neg["card4_delta"] == "二手反比新品偏貴"


# ==============================================================================
# 3. Plotly 圖表生成與屬性驗證 (Visualizations)
# ==============================================================================


class TestPlotlyChartsGeneration:
    """驗證新品價格折線圖與跨市場直條圖之 Trace 屬性、參考線與色碼。"""

    def test_price_trend_chart_with_data_and_reference_lines(self):
        """驗證新品 90 天走勢折線圖包含 Scatter Trace 與 3 條水平參考線。"""
        trend_info = {
            "product_id": 1,
            "max_price": 16500,
            "min_price": 13990,
            "avg_price": 15200.0,
            "current_price": 13990,
            "price_percentile": 0.0,
            "discount_authenticity": "真降價",
            "price_history": [
                {"record_date": "2026-10-01", "current_new_price": 16500},
                {"record_date": "2026-10-15", "current_new_price": 15500},
                {"record_date": "2026-11-01", "current_new_price": 13990},
            ],
        }
        fig = create_price_trend_chart(trend_info, "RTX 4060 Ti 16G")
        assert len(fig.data) == 1
        scatter_trace = fig.data[0]
        assert scatter_trace.mode == "lines+markers"
        assert list(scatter_trace.x) == ["2026-10-01", "2026-10-15", "2026-11-01"]
        assert list(scatter_trace.y) == [16500, 15500, 13990]
        assert scatter_trace.line.color == "#38BDF8"

        # 驗證 Layout 水平參考線與標籤
        assert "真降價" in fig.layout.title.text
        # add_hline 會在 layout.shapes 產生 line 物件
        assert len(fig.layout.shapes) == 3
        shape_colors = [s.line.color for s in fig.layout.shapes]
        assert "#FF7675" in shape_colors  # 歷史高價 (紅)
        assert "#34D399" in shape_colors  # 歷史低價 (綠)
        assert "#9CA3AF" in shape_colors  # 歷史均價 (灰)

    def test_platform_comparison_chart_with_data(self):
        """驗證跨市場價格直條圖之 4 大類別、Bar 屬性與差額標籤。"""
        platform_info = {
            "spec_group": "RTX 4060 Ti 16G",
            "groups": {
                "FB_Group_individual": {"mean_price": 11500.0, "count": 5},
                "PTT_individual": {"mean_price": 11000.0, "count": 3},
                "FB_Group_workshop": {"mean_price": 13000.0, "count": 2},
            },
        }
        new_min_price = 15000
        fig = create_platform_comparison_chart(
            platform_info, new_min_price, "RTX 4060 Ti 16G"
        )
        assert len(fig.data) == 1
        bar_trace = fig.data[0]
        assert list(bar_trace.x) == [
            "今日新品最低價",
            "FB 個人自售均價",
            "PTT 個人自售均價",
            "FB 工作室均價",
        ]
        assert list(bar_trace.y) == [15000, 11500, 11000, 13000]

        # 驗證文字標籤包含相對於新品價差
        assert "$15,000" in bar_trace.text[0]
        assert "-$3,500" in bar_trace.text[1]  # 11500 - 15000 = -3500
        assert "-$4,000" in bar_trace.text[2]  # 11000 - 15000 = -4000
        assert "-$2,000" in bar_trace.text[3]  # 13000 - 15000 = -2000


# ==============================================================================
# 4. 二手比價列表建構與過濾驗證 (Listings Table & Filters)
# ==============================================================================


class TestListingsTableAndFiltering:
    """驗證二手刊登過濾開關（排除工作室、僅看保內）及四級狀態徽章生成。"""

    def test_status_badge_mapping(self):
        """驗證四級行情評定狀態徽章精準映射。"""
        assert get_status_badge({"evaluation_level": "bargain"}) == "🟢 低於行情推薦"
        assert get_status_badge({"evaluation_level": "fair"}) == "🟡 符合行情"
        assert get_status_badge({"evaluation_level": "overpriced"}) == "⚪ 偏貴買新品"
        assert get_status_badge({"evaluation_level": "anomaly_low"}) == "🔴 異常低價警示"
        assert get_status_badge({"evaluation_level": "unknown"}) == "⚪ 暫無評估"
        assert get_status_badge({}) == "⚪ 暫無評估"

    def test_filter_listings_exclude_workshops(self, mem_db: sqlite3.Connection):
        """驗證 exclude_workshops 開關可精準過濾商業工作室。"""
        spec = "RTX 4060 Ti 16G"
        upsert_used_listing(
            mem_db,
            {
                "post_id": "p1",
                "spec_group": spec,
                "source_platform": "FB_Group",
                "seller_name": "Gamer1",
                "seller_type": "個人自售",
                "used_asking_price": 10000,
                "warranty_status": "保內",
                "post_time": "2026-11-01 10:00:00",
            },
        )
        upsert_used_listing(
            mem_db,
            {
                "post_id": "p2",
                "spec_group": spec,
                "source_platform": "FB_Group",
                "seller_name": "StudioX",
                "seller_type": "商業工作室",
                "used_asking_price": 12500,
                "warranty_status": "過保",
                "post_time": "2026-11-01 11:00:00",
            },
        )
        upsert_used_listing(
            mem_db,
            {
                "post_id": "p3",
                "spec_group": spec,
                "source_platform": "FB_Group",
                "seller_name": "MiningLab",
                "seller_type": "workshop",
                "used_asking_price": 9500,
                "warranty_status": "過保",
                "post_time": "2026-11-01 12:00:00",
            },
        )

        # 預設排除工作室：只留 Gamer1
        filtered_default = get_filtered_listings(mem_db, spec, exclude_workshops=True)
        assert len(filtered_default) == 1
        assert filtered_default[0]["seller_name"] == "Gamer1"

        # 不排除工作室：全部 3 筆
        filtered_all = get_filtered_listings(mem_db, spec, exclude_workshops=False)
        assert len(filtered_all) == 3

    def test_filter_listings_only_in_warranty(self, mem_db: sqlite3.Connection):
        """驗證 only_in_warranty 開關可保留保內/終保商品並排除過保。"""
        spec = "DDR5-6000 32G"
        upsert_used_listing(
            mem_db,
            {
                "post_id": "ram1",
                "spec_group": spec,
                "source_platform": "PTT",
                "seller_name": "User1",
                "seller_type": "個人自售",
                "used_asking_price": 2800,
                "warranty_status": "終身保固",
                "post_time": "2026-11-01 10:00:00",
            },
        )
        upsert_used_listing(
            mem_db,
            {
                "post_id": "ram2",
                "spec_group": spec,
                "source_platform": "PTT",
                "seller_name": "User2",
                "seller_type": "個人自售",
                "used_asking_price": 2500,
                "warranty_status": "原廠保內",
                "post_time": "2026-11-01 11:00:00",
            },
        )
        upsert_used_listing(
            mem_db,
            {
                "post_id": "ram3",
                "spec_group": spec,
                "source_platform": "PTT",
                "seller_name": "User3",
                "seller_type": "個人自售",
                "used_asking_price": 2000,
                "warranty_status": "過保",
                "post_time": "2026-11-01 12:00:00",
            },
        )

        # 僅看保內：保留 ram1 (終身保固) 與 ram2 (原廠保內)，排除 ram3 (過保)
        in_w = get_filtered_listings(
            mem_db, spec, exclude_workshops=False, only_in_warranty=True
        )
        assert len(in_w) == 2
        seller_names = [r["seller_name"] for r in in_w]
        assert "User1" in seller_names
        assert "User2" in seller_names
        assert "User3" not in seller_names

    def test_build_listings_dataframe_values_and_badges(
        self, mem_db: sqlite3.Connection
    ):
        """驗證 build_listings_dataframe 產生完整的表格欄位、折數與四級行情標籤。"""
        spec = "RTX 4060 Ti 16G"
        p_id = upsert_product(
            mem_db, "GPU", spec, "ASUS", "ASUS Dual RTX 4060 Ti 16G"
        )
        record_new_price(
            mem_db,
            p_id,
            "2026-11-01",
            15990,
            14990,
            "正常報價",
        )

        # 插入 4 筆不同價位的刊登，觸發不同評級
        # 1. 正常基準刊登 (12000 已售出，作為已售出中位基準)
        upsert_used_listing(
            mem_db,
            {
                "post_id": "post_base",
                "product_id": p_id,
                "spec_group": spec,
                "source_platform": "PTT",
                "seller_name": "BaseSeller",
                "seller_type": "個人自售",
                "used_asking_price": 12000,
                "warranty_status": "保內",
                "post_time": "2026-11-01 09:00:00",
                "sale_status": "已售出",
            },
        )
        # 2. 異常低價 (3000 < 50% * 12000 = 6000)
        upsert_used_listing(
            mem_db,
            {
                "post_id": "post_anomaly",
                "product_id": p_id,
                "spec_group": spec,
                "source_platform": "FB_Group",
                "seller_name": "Scammer",
                "seller_type": "個人自售",
                "used_asking_price": 3000,
                "warranty_status": "過保",
                "post_time": "2026-11-01 08:00:00",
                "sale_status": "刊登中",
            },
        )
        # 3. 推薦購買 (< 90% * 12000 = 10800, e.g. 10000)
        upsert_used_listing(
            mem_db,
            {
                "post_id": "post_bargain",
                "product_id": p_id,
                "spec_group": spec,
                "source_platform": "PTT",
                "seller_name": "GoodGuy",
                "seller_type": "個人自售",
                "used_asking_price": 10000,
                "warranty_status": "保內",
                "post_time": "2026-11-01 10:00:00",
                "sale_status": "刊登中",
            },
        )
        # 4. 偏貴建議買新品 (>= 90% * 14990 = 13491, e.g. 14000)
        upsert_used_listing(
            mem_db,
            {
                "post_id": "post_over",
                "product_id": p_id,
                "spec_group": spec,
                "source_platform": "FB_Group",
                "seller_name": "Expensive",
                "seller_type": "個人自售",
                "used_asking_price": 14000,
                "warranty_status": "保內",
                "post_time": "2026-11-01 11:00:00",
                "sale_status": "刊登中",
            },
        )

        listings = get_filtered_listings(mem_db, spec, exclude_workshops=False)
        df = build_listings_dataframe(listings, mem_db, spec, new_min_price=14990)
        assert len(df) == 4

        # 檢視標籤存在性
        badges = list(df["行情標籤"])
        assert "🔴 異常低價警示" in badges
        assert "🟢 低於行情推薦" in badges
        assert "⚪ 偏貴買新品" in badges

        # 檢驗折數欄位
        discounts = list(df["新品折數"])
        assert any("折" in d for d in discounts)

    def test_build_listings_dataframe_handles_dirty_data(
        self, mem_db: sqlite3.Connection
    ):
        """驗證 build_listings_dataframe 遭遇 None 價格或缺欄位髒資料時不崩潰 (ERR-007 防範)。"""
        dirty_listings = [
            {
                "source_platform": "FB_Group",
                "post_time": "2026-11-01 12:00:00",
                "seller_name": "DirtySeller",
                "seller_type": "個人自售",
                "used_asking_price": None,  # 空價格
                "warranty_status": None,
                "post_url": None,
            },
            {
                # 完全缺漏 used_asking_price 欄位
                "source_platform": "PTT",
                "seller_name": "MissingSeller",
            },
        ]
        df = build_listings_dataframe(dirty_listings, mem_db, "RTX 4060 Ti 16G", None)
        assert len(df) == 2
        assert df.iloc[0]["開價"] == 0
        assert df.iloc[1]["開價"] == 0


# ==============================================================================
# 5. Streamlit AppTest 無頭掛載與動態交互驗證 (AppTest Headless)
# ==============================================================================


class TestStreamlitAppTestHeadless:
    """利用 streamlit.testing.v1.AppTest 進行全方位無頭掛載與使用者互動模擬測試。"""

    @pytest.fixture
    def app_file_path(self) -> str:
        """回傳 app/main_app.py 絕對路徑。"""
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root_dir, "app", "main_app.py")

    def test_apptest_initial_render(self, app_file_path: str):
        """驗證 Streamlit 儀表板首次加載無未捕獲 Exception 且各元件正確掛載。"""
        at = AppTest.from_file(app_file_path)
        at.run()

        # 1. 斷言無任何例外崩潰
        assert len(at.exception) == 0

        # 2. 斷言標題與側邊欄元件
        assert len(at.title) >= 1
        assert "電腦硬體新品與二手市場比價分析系統" in at.title[0].value

        # 2組側邊欄下拉選單 (類別, 規格群組)
        assert len(at.sidebar.selectbox) == 2
        assert at.sidebar.selectbox[0].value == "GPU"

        # 2組側邊欄核取方塊 (排除商業工作室, 僅看原廠保固)
        assert len(at.sidebar.checkbox) == 2
        assert at.sidebar.checkbox[0].value is True  # 預設勾選排除工作室
        assert at.sidebar.checkbox[1].value is False  # 預設不勾選僅看保內

        # 1個即時同步按鈕
        assert len(at.sidebar.button) == 1

        # 4組重點指標卡
        assert len(at.metric) == 4
        labels = [m.label for m in at.metric]
        assert "今日新品最低價" in labels
        assert "二手市場中位價" in labels
        assert "二手折數" in labels
        assert "轉買二手可省金額" in labels

    def test_apptest_category_change_interaction(self, app_file_path: str):
        """模擬使用者切換側邊欄大類（GPU -> RAM），驗證規格群組選單即時聯動更新且無錯誤。"""
        at = AppTest.from_file(app_file_path)
        at.run()
        assert len(at.exception) == 0

        # 切換類別為 RAM
        at.sidebar.selectbox[0].select("RAM")
        at.run()

        assert len(at.exception) == 0
        assert at.sidebar.selectbox[0].value == "RAM"
        # 驗證規格群組已聯動更新為 RAM 相關規格
        spec_options = at.sidebar.selectbox[1].options
        assert any("DDR" in opt for opt in spec_options)

    def test_apptest_filter_toggles(self, app_file_path: str):
        """模擬使用者切換篩選開關（取消排除工作室、勾選僅看保內），驗證狀態正常更新無崩潰。"""
        at = AppTest.from_file(app_file_path)
        at.run()
        assert len(at.exception) == 0

        # 取消排除工作室
        at.sidebar.checkbox[0].uncheck()
        # 勾選僅看保內
        at.sidebar.checkbox[1].check()
        at.run()

        assert len(at.exception) == 0
        assert at.sidebar.checkbox[0].value is False
        assert at.sidebar.checkbox[1].value is True

    def test_apptest_sync_button_click_success(self, app_file_path: str):
        """模擬點擊側邊欄同步按鈕，Mock 外部爬蟲連線，驗證同步成功無異常。"""
        at = AppTest.from_file(app_file_path)
        at.run()
        assert len(at.exception) == 0

        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=35), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=12):
            at.sidebar.button[0].click().run()

        assert len(at.exception) == 0

    def test_apptest_sync_button_click_error_handling(self, app_file_path: str):
        """模擬外部爬蟲連線失敗拋出例外，驗證側邊欄優雅捕獲顯示錯誤訊息而不崩潰。"""
        at = AppTest.from_file(app_file_path)
        at.run()
        assert len(at.exception) == 0

        with patch(
            "crawlers.coolpc_crawler.sync_coolpc_to_db",
            side_effect=RuntimeError("連線原價屋超時 (Timeout)"),
        ), patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=0):
            at.sidebar.button[0].click().run()

        # 驗證未引發未捕獲例外（App 層級捕獲並透過 st.sidebar.error 呈現）
        assert len(at.exception) == 0
        assert len(at.sidebar.error) >= 1
        assert "同步失敗" in at.sidebar.error[0].value

    def test_apptest_sidebar_sync_status_display(
        self, app_file_path: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證側邊欄頂部同步控制中心於不同同步日誌狀態下的呈現文字。"""
        db_file = tmp_path / "sync_status_test.db"
        conn = init_db(str(db_file))
        record_sync_log(
            conn,
            status="SUCCESS",
            details="原價屋: 50 筆 · PTT: 20 筆 · FB: 5 筆",
            coolpc_count=50,
            ptt_count=20,
            fb_count=5,
            sync_time="2026-11-03T21:35:10.123456",
        )
        conn.close()

        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        at = AppTest.from_file(app_file_path)
        at.run()

        assert len(at.exception) == 0
        # 驗證側邊欄包含同步控制中心 subheader
        sidebar_subheaders = [s.value for s in at.sidebar.subheader]
        assert any("同步控制中心" in s for s in sidebar_subheaders)

        # 驗證側邊欄呈現格式化時間、成功徽章與筆數摘要
        captions = [c.value for c in at.sidebar.caption]
        caption_text = " ".join(captions)
        assert "🟢 同步成功" in caption_text
        assert "2026-11-03 21:35:10" in caption_text
        assert "原價屋: 50 筆" in caption_text
        assert "PTT: 20 筆" in caption_text
        assert "FB: 5 筆" in caption_text

    def test_apptest_startup_auto_sync_session_state_protection(
        self, app_file_path: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證啟動自動同步在 session_state 防重機制下僅執行一次。"""
        db_file = tmp_path / "auto_sync_test.db"
        conn = init_db(str(db_file))
        conn.close()
        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        monkeypatch.setenv("ENABLE_TEST_AUTO_SYNC", "1")

        with patch("services.sync_service.execute_sync", return_value={"status": "SUCCESS"}) as mock_sync:
            at = AppTest.from_file(app_file_path)
            at.run()
            # 首次加載應執行自動同步
            assert mock_sync.call_count == 1

            # 再次互動（如切換類別）時不應重複觸發自動同步
            at.sidebar.selectbox[0].select("RAM")
            at.run()
            assert mock_sync.call_count == 1

