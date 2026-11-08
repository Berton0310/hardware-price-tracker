"""全系統端到端整合測試 (tests/test_integration.py)

遵循 CLAUDE.md、AGENTS.md 與 Milestone 5 全系統整合閉環規範。
涵蓋三大核心驗收面向：
1. 端到端完整管線閉環 (End-to-End Pipeline)：
   - 爬蟲資料注入 -> 資料庫原子寫入 -> 統計與行情運算 -> Streamlit 儀表板無頭元件消費。
2. 冪等性與重複寫入強韌性 (Pipeline Idempotency)：
   - 主檔去重、歷史價格同日覆蓋、二手貼文狀態原子更新。
3. 全流程運行腳本 (run_pipeline.py) CLI 介面與分派驗證。
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from analysis.price_analysis import (
    analyze_new_price_trend,
    calculate_spec_group_discount,
    compare_platforms_and_sellers,
    evaluate_listing_price,
)
from cleaners.llm_parser import parse_post_with_llm
from crawlers.coolpc_crawler import sync_coolpc_to_db
from crawlers.ptt_crawler import sync_ptt_to_db
from database.db_manager import (
    init_db,
    record_new_price,
    upsert_product,
    upsert_used_listing,
)
from run_pipeline import (
    build_cli_parser,
    launch_dashboard,
    main as pipeline_main,
    print_analysis_report,
    sync_coolpc,
    sync_ptt,
)

# ==============================================================================
# Mock 資料集定義
# ==============================================================================

MOCK_COOLPC_MULTI_CATEGORY_HTML = """
<!DOCTYPE html>
<html>
<body>
<select name="n12">
    <option value="0">--- 顯示卡 ---</option>
    <optgroup label="NVIDIA RTX4060Ti-16GB">
        <option value="101">微星 RTX 4060 Ti VENTUS 2X BLACK 16G OC(2565MHz/19.9cm/雙風扇/註四年), $15990↘$14490 ◆ ★</option>
        <option value="102">技嘉 RTX 4060 Ti GAMING OC 16G(2565MHz/28.1cm/三風扇/註五年), $16990↘$15490 ◆ ★</option>
    </optgroup>
</select>
<select name="n6">
    <option value="0">--- 記憶體 ---</option>
    <optgroup label="DDR5 桌上型記憶體">
        <option value="201">美光 Crucial PRO 32GB(16GB*2) DDR5 6000/CL36/黑【雙參數/終身保】, ↘$2850 ◆ ★</option>
        <option value="202">金士頓 32GB(雙通16GB*2) DDR5-6000 FURY Beast 黑(CL30/支援XMP&EXPO), $3350↘$3150 ★</option>
    </optgroup>
</select>
<select name="n7">
    <option value="0">--- M.2 SSD ---</option>
    <optgroup label="M.2 PCIe 4.0 SSD">
        <option value="301">美光 Micron Crucial T500 1TB/Gen4 PCIe 4.0/讀:7300M/寫:6800M/五年保, $2999↘$2699 ★</option>
    </optgroup>
</select>
<select name="n4">
    <option value="0">--- 處理器 ---</option>
    <optgroup label="Intel 第14代 Raptor Lake-R">
        <option value="401">Intel i7-14700K【20核/28緒】3.4GHz(↑5.6GHz)/33M/UHD770/無風扇/125W【代理公司貨】, $14800↘$13900 ★</option>
    </optgroup>
</select>
</body>
</html>
"""

MOCK_PTT_HARDWARESALE_LIST_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="r-list-container action-bar-margin bbs-screen">
    <div class="r-ent">
        <div class="title">
            <a href="/bbs/HardwareSale/M.1712345678.A.001.html">[賣/台北/面交] 微星 RTX 4060 Ti 16G 近全新保內</a>
        </div>
        <div class="meta"><div class="author">gamer_pro</div></div>
    </div>
    <div class="r-ent">
        <div class="title">
            <a href="/bbs/HardwareSale/M.1712345679.A.002.html">[賣/全國/郵寄] 金士頓 FURY DDR5-6000 32G 終保</a>
        </div>
        <div class="meta"><div class="author">ram_master</div></div>
    </div>
</div>
</body>
</html>
"""

MOCK_PTT_POST_GPU_HTML = """
<!DOCTYPE html>
<html><body><div id="main-content" class="bbs-screen bbs-content">
<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">gamer_pro</span></div>
<div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/台北/面交] 微星 RTX 4060 Ti 16G 近全新保內</span></div>
<div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Sun Nov  1 14:00:00 2026</span></div>
◎硬體型號：微星 RTX 4060 Ti VENTUS 2X BLACK 16G OC
◎欲售價格：11500
◎品樣狀況：盒裝完整，原廠保內至 2027 年底
◎交易方式：捷運站面交
<div class="push"><span class="push-tag">推 </span><span class="push-userid">buyer_one</span><span class="push-content">: 排</span></div>
</div></body></html>
"""

MOCK_PTT_POST_RAM_HTML = """
<!DOCTYPE html>
<html><body><div id="main-content" class="bbs-screen bbs-content">
<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">ram_master</span></div>
<div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/全國/郵寄] 金士頓 FURY DDR5-6000 32G 終保</span></div>
<div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Sun Nov  1 15:30:00 2026</span></div>
◎硬體型號：金士頓 DDR5-6000 32G (16G*2)
◎欲售價格：2400
◎品樣狀況：升級換下，捷元代理終身保固
◎交易方式：7-11 店到店
</div></body></html>
"""


# ==============================================================================
# 1. 端到端完整管線閉環 (End-to-End Pipeline)
# ==============================================================================


class TestEndToEndPipeline:
    """驗證端到端完整管線閉環：
    爬蟲採集注入 -> 資料庫原子入庫 -> 統計分析計算 -> Streamlit 儀表板無頭完整消費。
    """

    @pytest.fixture
    def app_file_path(self) -> str:
        """回傳 app/main_app.py 絕對路徑。"""
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root_dir, "app", "main_app.py")

    def test_e2e_full_pipeline_data_flow(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, app_file_path: str
    ):
        """端到端測試：
        1. 建立全新暫存 SQLite。
        2. 原價屋爬蟲注入 4 大分類新品價格與歷史走勢資料。
        3. PTT 與多平台二手貼文注入（含工作室、個人自售、不同保固狀態與極端開價）。
        4. 執行 M3 四大分析函式驗證運算指標之正確性與閉環性。
        5. 透過 Streamlit AppTest 掛載此資料庫，驗證前端介面無異常渲染。
        """
        db_file = tmp_path / "e2e_hardware.db"
        conn = init_db(str(db_file))

        # --- 步驟 1: 原價屋爬蟲新品注入 ---
        synced_coolpc = sync_coolpc_to_db(conn, html=MOCK_COOLPC_MULTI_CATEGORY_HTML)
        assert synced_coolpc == 6  # 2 GPU, 2 RAM, 1 SSD, 1 CPU

        # 查驗產品主檔完整性
        cur = conn.execute("SELECT category, spec_group, model_name FROM products ORDER BY category;")
        prod_rows = cur.fetchall()
        assert len(prod_rows) == 6
        cats = {r[0] for r in prod_rows}
        assert cats == {"GPU", "RAM", "SSD", "CPU"}

        # 獲取代表性 GPU 產品 ID，並注入 90 天前與 30 天前歷史價格走勢，以利檢驗真假降價
        cur = conn.execute(
            "SELECT product_id FROM products WHERE model_name LIKE '%RTX 4060 Ti VENTUS 2X BLACK 16G%';"
        )
        gpu_prod_id = cur.fetchone()[0]

        # 注入 60 天前、30 天前與今日價格 (16500 -> 15500 -> 14490)
        today = datetime.date.today()
        d_60 = (today - datetime.timedelta(days=60)).isoformat()
        d_30 = (today - datetime.timedelta(days=30)).isoformat()
        record_new_price(conn, gpu_prod_id, d_60, listed_price=16500, current_price=16500, change_tag="不變")
        record_new_price(conn, gpu_prod_id, d_30, listed_price=16500, current_price=15500, change_tag="↘降價")

        # --- 步驟 2: PTT 爬蟲與二手市場貼文注入 ---
        mock_list_resp = MagicMock()
        mock_list_resp.text = MOCK_PTT_HARDWARESALE_LIST_HTML
        mock_list_resp.raise_for_status = MagicMock()

        mock_post_gpu_resp = MagicMock()
        mock_post_gpu_resp.text = MOCK_PTT_POST_GPU_HTML
        mock_post_gpu_resp.raise_for_status = MagicMock()

        mock_post_ram_resp = MagicMock()
        mock_post_ram_resp.text = MOCK_PTT_POST_RAM_HTML
        mock_post_ram_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.side_effect = [
            mock_list_resp,
            mock_post_gpu_resp,
            mock_post_ram_resp,
        ]

        with patch("crawlers.ptt_crawler._get_ptt_session", return_value=mock_session):
            synced_ptt = sync_ptt_to_db(
                conn=conn,
                max_pages=1,
                llm_parser_func=parse_post_with_llm,
                sleep_range=(0, 0),
            )
            assert synced_ptt == 2

        # 擴充注入多元二手貼文（驗證工作室溢價、偏貴、超低價警示、保固各狀態）
        # 1. 二手工作室偏貴刊登 (13500 >= 14490 * 0.9 = 13041)
        upsert_used_listing(
            conn,
            {
                "post_id": "fb_studio_gpu_1",
                "product_id": gpu_prod_id,
                "spec_group": "RTX 4060 Ti 16G",
                "source_platform": "FB_Group",
                "seller_name": "胖達二手工作室",
                "seller_type": "二手工作室",
                "used_asking_price": 13500,
                "warranty_status": "店保30天",
                "trade_method": "店面自取",
                "comment_count": 5,
                "sale_status": "販售中",
                "post_time": f"{today.isoformat()} 10:00:00",
                "post_url": "https://facebook.com/groups/pcmarket/1",
            },
        )
        # 2. 異常低價警示貼文 (5000 < 50% * 11500 = 5750)
        upsert_used_listing(
            conn,
            {
                "post_id": "fb_anomaly_gpu_2",
                "product_id": gpu_prod_id,
                "spec_group": "RTX 4060 Ti 16G",
                "source_platform": "FB_Group",
                "seller_name": "SusSeller",
                "seller_type": "個人自售",
                "used_asking_price": 5000,
                "warranty_status": "過保",
                "trade_method": "先匯款後寄送",
                "comment_count": 1,
                "sale_status": "販售中",
                "post_time": f"{today.isoformat()} 11:00:00",
                "post_url": "https://facebook.com/groups/pcmarket/2",
            },
        )
        # 3. 符合行情貼文 (11800)
        upsert_used_listing(
            conn,
            {
                "post_id": "fb_fair_gpu_3",
                "product_id": gpu_prod_id,
                "spec_group": "RTX 4060 Ti 16G",
                "source_platform": "FB_Group",
                "seller_name": "NormalGamer",
                "seller_type": "個人自售",
                "used_asking_price": 11800,
                "warranty_status": "原廠保內",
                "trade_method": "面交",
                "comment_count": 2,
                "sale_status": "販售中",
                "post_time": f"{today.isoformat()} 12:00:00",
                "post_url": "https://facebook.com/groups/pcmarket/3",
            },
        )

        # 查驗二手資料庫總筆數
        cur = conn.execute("SELECT COUNT(*) FROM used_market_listings;")
        assert cur.fetchone()[0] == 5

        # --- 步驟 3: 執行 M3 四大分析函式驗收 ---
        # 3.1 歷史價格走勢與真假降價
        trend = analyze_new_price_trend(conn, gpu_prod_id, days=90)
        assert trend["current_price"] == 14490
        assert trend["min_price"] == 14490
        assert trend["max_price"] == 16500
        assert trend["price_percentile"] == 0.0  # 處於歷史最低點 (0%)
        assert trend["discount_authenticity"] == "創近期新低 (真降價)"
        assert len(trend["price_history"]) >= 3

        # 3.2 二手折數與價差計算
        discount = calculate_spec_group_discount(conn, "RTX 4060 Ti 16G")
        assert discount["new_min_price"] == 14490
        assert discount["used_median_price"] is not None
        assert discount["used_median_price"] > 0
        assert discount["discount_ratio"] is not None
        assert 0 < discount["discount_ratio"] <= 100.0
        assert discount["saved_amount"] is not None
        assert discount["saved_amount"] > 0

        # 3.3 跨平台與賣家型態分析
        comp = compare_platforms_and_sellers(conn, "RTX 4060 Ti 16G")
        assert comp["groups"]["PTT_individual"]["count"] >= 1
        assert comp["groups"]["FB_Group_individual"]["count"] >= 1
        assert comp["groups"]["FB_Group_workshop"]["count"] >= 1
        # 工作室平均價格應高於個人自售 (溢價存在)
        assert comp["workshop_premium_rate"] is not None
        assert comp["workshop_premium_rate"] > 0

        # 3.4 四級行情評估
        # 異常低價
        eval_low = evaluate_listing_price(conn, "RTX 4060 Ti 16G", 5000)
        assert eval_low["evaluation_level"] == "anomaly_low"
        assert "異常低價" in eval_low["evaluation_label"]

        # 偏貴買新品
        eval_over = evaluate_listing_price(conn, "RTX 4060 Ti 16G", 13500)
        assert eval_over["evaluation_level"] == "overpriced"
        assert "偏貴" in eval_over["evaluation_label"]

        # 符合行情
        eval_fair = evaluate_listing_price(conn, "RTX 4060 Ti 16G", 11800)
        assert eval_fair["evaluation_level"] in ("fair", "bargain")

        conn.close()

        # --- 步驟 4: Streamlit 無頭 AppTest 介面消費驗收 ---
        # 透過環境變數注入剛產生的資料庫
        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))

        at = AppTest.from_file(app_file_path)
        at.run(timeout=10)

        # 4.1 無任何未捕獲例外
        assert len(at.exception) == 0

        # 4.2 頂部指標卡真實數值驗證 (非空狀態)
        assert len(at.metric) == 4
        assert "$14,490" in at.metric[0].value
        assert "折" in at.metric[2].value
        assert "$" in at.metric[3].value

        # 4.3 規格群組動態由資料庫生成
        spec_options = at.sidebar.selectbox[1].options
        assert "RTX 4060 Ti 16G" in spec_options

        # 4.4 類別切換互動（切換至 RAM）
        at.sidebar.selectbox[0].select("RAM")
        at.run(timeout=10)
        assert len(at.exception) == 0
        ram_spec_options = at.sidebar.selectbox[1].options
        assert any("DDR5" in opt for opt in ram_spec_options)

        # 4.5 篩選開關測試（切回 GPU 並勾選僅看保內）
        at.sidebar.selectbox[0].select("GPU")
        at.sidebar.checkbox[1].check()
        at.run(timeout=10)
        assert len(at.exception) == 0


# ==============================================================================
# 2. 冪等性與重複寫入強韌性 (Pipeline Idempotency)
# ==============================================================================


class TestPipelineIdempotency:
    """驗證資料庫寫入在多次重複執行下的冪等性：
    同一產品、同一日價格、同一篇貼文重複寫入不引發異常，資料不膨脹且準確更新。
    """

    @pytest.fixture
    def mem_db(self) -> sqlite3.Connection:
        """提供乾淨的 In-Memory SQLite 資料庫。"""
        return init_db(":memory:")

    def test_coolpc_sync_idempotency(self, mem_db: sqlite3.Connection):
        """原價屋爬蟲多次重複同步：產品數不重複增長，同日價格正確覆蓋更新。"""
        # 第 1 次同步
        c1 = sync_coolpc_to_db(mem_db, html=MOCK_COOLPC_MULTI_CATEGORY_HTML)
        assert c1 == 6

        cur = mem_db.execute("SELECT COUNT(*) FROM products;")
        prod_count_1 = cur.fetchone()[0]
        cur = mem_db.execute("SELECT COUNT(*) FROM new_price_history;")
        hist_count_1 = cur.fetchone()[0]
        assert prod_count_1 == 6
        assert hist_count_1 == 6

        # 第 2 次同步（相同資料）
        c2 = sync_coolpc_to_db(mem_db, html=MOCK_COOLPC_MULTI_CATEGORY_HTML)
        assert c2 == 6

        cur = mem_db.execute("SELECT COUNT(*) FROM products;")
        assert cur.fetchone()[0] == prod_count_1  # 產品主檔筆數完全不變
        cur = mem_db.execute("SELECT COUNT(*) FROM new_price_history;")
        assert cur.fetchone()[0] == hist_count_1  # 歷史價格筆數不膨脹

    def test_used_listing_upsert_idempotency(self, mem_db: sqlite3.Connection):
        """二手貼文重複寫入：post_id 衝突時自動更新售價與販售狀態，不拋出主鍵衝突。"""
        post = {
            "post_id": "test_post_unique_001",
            "spec_group": "RTX 4060 Ti 16G",
            "source_platform": "PTT HardwareSale",
            "seller_name": "GamerX",
            "seller_type": "個人自售",
            "used_asking_price": 12500,
            "warranty_status": "原廠保內",
            "trade_method": "面交",
            "comment_count": 0,
            "sale_status": "販售中",
            "post_time": "2026-11-01 10:00:00",
            "post_url": "https://ptt.cc/1",
        }
        # 第 1 次寫入
        upsert_used_listing(mem_db, post)
        cur = mem_db.execute("SELECT COUNT(*), used_asking_price, sale_status FROM used_market_listings;")
        r1 = cur.fetchone()
        assert r1[0] == 1
        assert r1[1] == 12500
        assert r1[2] == "販售中"

        # 第 2 次寫入（降價至 11000 且改為已售出）
        post["used_asking_price"] = 11000
        post["sale_status"] = "已售出"
        post["comment_count"] = 5
        upsert_used_listing(mem_db, post)

        cur = mem_db.execute(
            "SELECT COUNT(*), used_asking_price, sale_status, comment_count FROM used_market_listings;"
        )
        r2 = cur.fetchone()
        assert r2[0] == 1  # 筆數維持 1
        assert r2[1] == 11000  # 價格已覆蓋更新
        assert r2[2] == "已售出"  # 狀態已更新
        assert r2[3] == 5


# ==============================================================================
# 3. 全流程運行腳本 (run_pipeline.py) CLI 介面與分派驗收
# ==============================================================================


class TestRunPipelineCLI:
    """驗證專案根目錄全流程執行腳本 run_pipeline.py 之模組介面與 CLI 旗標。"""

    def test_sync_coolpc_wrapper(self, tmp_path: Any):
        """測試 run_pipeline.sync_coolpc 包裝函式與成功輸出。"""
        db_path = str(tmp_path / "pipe_coolpc.db")
        with patch("run_pipeline.sync_coolpc_to_db", return_value=12):
            cnt = sync_coolpc(db_path)
            assert cnt == 12

    def test_sync_ptt_wrapper(self, tmp_path: Any):
        """測試 run_pipeline.sync_ptt 包裝函式與成功輸出。"""
        db_path = str(tmp_path / "pipe_ptt.db")
        with patch("run_pipeline.sync_ptt_to_db", return_value=8):
            cnt = sync_ptt(db_path, max_pages=1)
            assert cnt == 8

    def test_print_analysis_report_empty_db(self, tmp_path: Any, capsys: pytest.CaptureFixture):
        """測試 print_analysis_report 面對空資料庫時安全回傳空列表。"""
        db_path = str(tmp_path / "empty.db")
        res = print_analysis_report(db_path)
        assert res == []
        captured = capsys.readouterr()
        assert "目前為空" in captured.out

    def test_print_analysis_report_populated_db(self, tmp_path: Any, capsys: pytest.CaptureFixture):
        """測試 print_analysis_report 包含資料時正確輸出格式化報表。"""
        db_path = str(tmp_path / "populated.db")
        conn = init_db(db_path)
        p_id = upsert_product(
            conn,
            category="GPU",
            spec_group="RTX 4060 Ti 16G",
            brand="MSI",
            model_name="MSI RTX 4060 Ti 16G",
        )
        record_new_price(conn, p_id, "2026-11-01", 15000, 14000)
        upsert_used_listing(
            conn,
            {
                "post_id": "p_01",
                "product_id": p_id,
                "spec_group": "RTX 4060 Ti 16G",
                "source_platform": "PTT HardwareSale",
                "seller_name": "gamer",
                "seller_type": "個人自售",
                "used_asking_price": 11500,
                "warranty_status": "保內",
                "post_time": "2026-11-01 10:00:00",
                "post_url": "https://ptt.cc",
            },
        )
        conn.close()

        res = print_analysis_report(db_path)
        assert len(res) == 1
        assert res[0]["spec_group"] == "RTX 4060 Ti 16G"
        assert res[0]["new_min_price"] == 14000
        assert res[0]["used_median_price"] == 11500

        captured = capsys.readouterr()
        assert "比價分析摘要報告" in captured.out
        assert "RTX 4060 Ti 16G" in captured.out

    def test_launch_dashboard_invocation(self, tmp_path: Any):
        """測試 launch_dashboard 正確傳遞 HARDWARE_DB_PATH 環境變數並調用 streamlit run。"""
        db_path = str(tmp_path / "launch.db")
        with patch("subprocess.run") as mock_sub:
            launch_dashboard(db_path)
            assert mock_sub.called
            cmd = mock_sub.call_args[0][0]
            assert "streamlit" in cmd
            assert "run" in cmd
            env = mock_sub.call_args[1]["env"]
            assert env["HARDWARE_DB_PATH"] == db_path

    def test_cli_parser_flags(self):
        """測試 CLI 參數解析器所有旗標定義符合規格。"""
        parser = build_cli_parser()
        args = parser.parse_args(
            ["--sync-coolpc", "--sync-ptt", "--sync-daily", "--analysis", "--app", "--all"]
        )
        assert args.sync_coolpc is True
        assert args.sync_ptt is True
        assert args.sync_daily is True
        assert args.analysis is True
        assert args.app is True
        assert args.all is True

    def test_cli_dispatch_sync_coolpc(self, tmp_path: Any):
        """測試 pipeline_main 接收 --sync-coolpc 旗標時的分派行為。"""
        db_path = str(tmp_path / "cli_coolpc.db")
        with patch("sys.argv", ["run_pipeline.py", "--sync-coolpc", "--db", db_path]):
            with patch("run_pipeline.sync_coolpc", return_value=10) as mock_sync:
                pipeline_main()
                mock_sync.assert_called_once_with(db_path)

    def test_cli_dispatch_sync_daily(self, tmp_path: Any):
        """測試 pipeline_main 接收 --sync-daily 旗標時的分派行為。"""
        db_path = str(tmp_path / "cli_daily.db")
        with patch("sys.argv", ["run_pipeline.py", "--sync-daily", "--db", db_path, "--ptt-pages", "2"]):
            with patch("run_pipeline.sync_daily", return_value={"status": "SUCCESS"}) as mock_sync:
                pipeline_main()
                mock_sync.assert_called_once_with(db_path, ptt_pages=2)

    def test_cli_dispatch_all(self, tmp_path: Any):
        """測試 pipeline_main 接收 --all 旗標時的一鍵全流程串接。"""
        db_path = str(tmp_path / "cli_all.db")
        with patch("sys.argv", ["run_pipeline.py", "--all", "--db", db_path]):
            with patch("run_pipeline.sync_coolpc", return_value=10) as mock_coolpc:
                with patch("run_pipeline.sync_ptt", return_value=5) as mock_ptt:
                    with patch("run_pipeline.print_analysis_report", return_value=[]) as mock_rpt:
                        with patch("run_pipeline.launch_dashboard") as mock_dash:
                            pipeline_main()
                            mock_coolpc.assert_called_once_with(db_path)
                            mock_ptt.assert_called_once_with(db_path, max_pages=1)
                            mock_rpt.assert_called_once_with(db_path)
                            mock_dash.assert_called_once_with(db_path)
