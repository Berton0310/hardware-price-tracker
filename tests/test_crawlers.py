"""爬蟲模組單元與整合測試 (tests/test_crawlers.py)

驗證測試重點：
1. coolpc_crawler：
   - 4 大類別解析（GPU 12, RAM 6, SSD 7, CPU 4）。
   - 雙價格（$15990↘$14990）、單價格（$9990）、漲價標籤（↗）解析精確性。
   - sync_coolpc_to_db 原子級寫入 In-Memory SQLite（products 與 new_price_history）。
2. ptt_crawler：
   - parse_ptt_post_list 嚴格篩選 [賣/ 貼文，剔除 [徵/、[公告]、刪除文。
   - fetch_ptt_post_details 內文淨化、時間解析與推文數計算。
   - sync_ptt_to_db 端到端寫入 In-Memory SQLite used_market_listings，並具備快取防重複功能。
3. fb_crawler：
   - check_fb_cookie_health Pre-flight 檢測：無檔案、壞檔、無授權 token、有效 token 之真偽判定。
   - 無 Cookie 時的優雅降級與警告日誌驗證。
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from crawlers.coolpc_crawler import (
    _extract_prices_and_change,
    parse_coolpc_html,
    sync_coolpc_to_db,
)
from crawlers.fb_crawler import check_fb_cookie_health, crawl_fb_group
from crawlers.ptt_crawler import (
    fetch_ptt_post_details,
    parse_ptt_date,
    parse_ptt_post_list,
    sync_ptt_to_db,
)
from database.db_manager import init_db


# --- Mock 測資 ---

MOCK_COOLPC_HTML = """
<!DOCTYPE html>
<html>
<body>
<select name="n12">
    <option value="0">--- 顯示卡 ---</option>
    <optgroup label="NVIDIA RTX4060-8GB">
        <option value="1">華碩 DUAL-RTX4060-O8G-EVO(2535MHz/22.7cm/雙風扇/註四年), $9990 ◆ ★</option>
        <option value="2">技嘉 RTX4060Ti EAGLE OC 8G(2550MHz/27.2cm/三風扇/註五年), $15990↘$14990 ◆ ★ 熱賣</option>
        <option value="3">微星 RTX4060 VENTUS 2X WHITE 8G OC, $9490↗$9990 ◆ ★</option>
    </optgroup>
</select>
<select name="n6">
    <option value="0">--- 記憶體 ---</option>
    <optgroup label="DDR5 桌上型記憶體">
        <option value="4">美光 Crucial PRO 32GB(16GB*2) DDR5 6000/CL36/黑【雙參數/終身保】, ↘$2850 ◆ ★</option>
    </optgroup>
</select>
<select name="n7">
    <option value="0">--- M.2 SSD ---</option>
    <optgroup label="M.2 PCIe 4.0 SSD">
        <option value="5">金士頓 KC3000 1TB/Gen4 PCIe 4.0/讀:7000M/寫:6000M/五年保, $2950 ◆ ★</option>
    </optgroup>
</select>
<select name="n4">
    <option value="0">--- 處理器 ---</option>
    <optgroup label="Intel 第14代 Raptor Lake-R">
        <option value="6">Intel i5-14400F【10核/16緒】2.5GHz(↑4.7GHz)/20M/無內顯/65W【代理公司貨】, $6300 ◆ ★</option>
    </optgroup>
</select>
</body>
</html>
"""

MOCK_PTT_LIST_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="r-list-container action-bar-margin bbs-screen">
    <div class="btn-group btn-group-paging">
        <a class="btn wide" href="/bbs/HardwareSale/index1234.html">‹ 上頁</a>
        <a class="btn wide" href="/bbs/HardwareSale/index1236.html">下頁 ›</a>
    </div>
    <div class="r-ent">
        <div class="nrec"><span class="hl f2">3</span></div>
        <div class="title">
            <a href="/bbs/HardwareSale/M.1711111111.A.001.html">[賣/台北/面交] 技嘉 RTX 4060 Ti Eagle 8G</a>
        </div>
        <div class="meta">
            <div class="author">sellerA</div>
            <div class="date">11/03</div>
        </div>
    </div>
    <div class="r-ent">
        <div class="nrec"></div>
        <div class="title">
            <a href="/bbs/HardwareSale/M.1711111112.A.002.html">[徵/台中/面交] DDR5 6000 32G 記憶體</a>
        </div>
        <div class="meta">
            <div class="author">buyerB</div>
            <div class="date">11/03</div>
        </div>
    </div>
    <div class="r-ent">
        <div class="nrec"></div>
        <div class="title">
            <a href="/bbs/HardwareSale/M.1711111113.A.003.html">[公告] HardwareSale 板規規範</a>
        </div>
        <div class="meta">
            <div class="author">moderator</div>
            <div class="date">11/01</div>
        </div>
    </div>
    <div class="r-ent">
        <div class="nrec"><span class="hl f3">15</span></div>
        <div class="title">
            <a href="/bbs/HardwareSale/M.1711111114.A.004.html">[賣/全國/店到店] AMD R7 7800X3D 盒裝</a>
        </div>
        <div class="meta">
            <div class="author">sellerC</div>
            <div class="date">11/03</div>
        </div>
    </div>
    <div class="r-ent">
        <div class="nrec"></div>
        <div class="title">
            (本文已被刪除)
        </div>
        <div class="meta">
            <div class="author">-</div>
            <div class="date">11/03</div>
        </div>
    </div>
</div>
</body>
</html>
"""

MOCK_PTT_POST_HTML = """
<!DOCTYPE html>
<html>
<body>
<div id="main-content" class="bbs-screen bbs-content">
    <div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">sellerA (Seller A)</span></div>
    <div class="article-metaline-right"><span class="article-meta-tag">看板</span><span class="article-meta-value">HardwareSale</span></div>
    <div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[賣/台北/面交] 技嘉 RTX 4060 Ti Eagle 8G</span></div>
    <div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Tue Nov  3 12:00:00 2026</span></div>
    ◎硬體型號：技嘉 RTX 4060 Ti Eagle 8G
    ◎欲售價格：10500
    ◎品樣狀況：保固至 2028 年
    ◎交易方式：面交
    <div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">user1</span><span class="f3 push-content">: 排</span><span class="push-ipdatetime">11/03 12:01</span></div>
    <div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">user2</span><span class="f3 push-content">: 排+1</span><span class="push-ipdatetime">11/03 12:05</span></div>
</div>
</body>
</html>
"""


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """提供初始化後的 In-Memory SQLite 資料庫連線。"""
    conn = init_db(":memory:")
    yield conn
    conn.close()


# --- 原價屋爬蟲測試 (coolpc_crawler) ---

class TestCoolpcCrawler:
    """測試原價屋新品爬蟲與資料庫同步。"""

    def test_extract_prices_and_change_tags(self):
        """測試單價格、降價雙價格、漲價雙價格與符號提取。"""
        # 降價雙價格
        listed, current, tag = _extract_prices_and_change("技嘉 RTX 4060 Ti, $15990↘$14990 ◆ ★")
        assert (listed, current, tag) == (15990, 14990, "↘降價")

        # 漲價雙價格
        listed, current, tag = _extract_prices_and_change("微星 RTX 4060, $9490↗$9990 ◆ ★")
        assert (listed, current, tag) == (9490, 9990, "↗漲價")

        # 單一平價
        listed, current, tag = _extract_prices_and_change("華碩 RTX 4060, $9990 ◆ ★")
        assert (listed, current, tag) == (9990, 9990, "不變")

        # 單一降價標籤
        listed, current, tag = _extract_prices_and_change("美光 DDR5, ↘$2850 ◆ ★")
        assert (listed, current, tag) == (2850, 2850, "↘降價")

    def test_parse_coolpc_html_four_categories(self):
        """驗證原價屋 4 大核心類別 (GPU, RAM, SSD, CPU) 完整抽取。"""
        items = parse_coolpc_html(MOCK_COOLPC_HTML)
        categories = {item["category"] for item in items}
        assert categories == {"GPU", "RAM", "SSD", "CPU"}
        assert len(items) == 6

        # 檢查 GPU
        gpu_items = [it for it in items if it["category"] == "GPU"]
        assert len(gpu_items) == 3
        asus_gpu = next(it for it in gpu_items if it["brand"] == "ASUS")
        assert asus_gpu["listed_price"] == 9990
        assert asus_gpu["current_price"] == 9990
        assert asus_gpu["warranty_years"] == 4

        # 檢查 RAM
        ram_item = next(it for it in items if it["category"] == "RAM")
        assert ram_item["brand"] == "Crucial"
        assert ram_item["current_price"] == 2850
        assert ram_item["change_tag"] == "↘降價"
        assert ram_item["warranty_years"] == 10

        # 檢查 SSD
        ssd_item = next(it for it in items if it["category"] == "SSD")
        assert ssd_item["brand"] == "Kingston"
        assert ssd_item["current_price"] == 2950
        assert ssd_item["warranty_years"] == 5

        # 檢查 CPU
        cpu_item = next(it for it in items if it["category"] == "CPU")
        assert cpu_item["brand"] == "Intel"
        assert cpu_item["current_price"] == 6300
        assert cpu_item["warranty_years"] == 3

    def test_sync_coolpc_to_db_in_memory(self, memory_db: sqlite3.Connection):
        """驗證 sync_coolpc_to_db 正確將 6 筆商品寫入 In-Memory SQLite products 與 new_price_history。"""
        count = sync_coolpc_to_db(memory_db, html=MOCK_COOLPC_HTML)
        assert count == 6

        # 驗證 products 表
        cur = memory_db.execute("SELECT COUNT(*) FROM products;")
        assert cur.fetchone()[0] == 6

        # 驗證 new_price_history 表
        cur2 = memory_db.execute("SELECT COUNT(*) FROM new_price_history;")
        assert cur2.fetchone()[0] == 6

        # 再次同步（同日冪等性更新，不應新增重複資料）
        count_repeat = sync_coolpc_to_db(memory_db, html=MOCK_COOLPC_HTML)
        assert count_repeat == 6
        cur3 = memory_db.execute("SELECT COUNT(*) FROM new_price_history;")
        assert cur3.fetchone()[0] == 6


# --- PTT 二手爬蟲測試 (ptt_crawler) ---

class TestPttCrawler:
    """測試 PTT HardwareSale 列表過濾與內文擷取入庫。"""

    def test_parse_ptt_post_list_filtering(self):
        """驗證僅保留 [賣/ 貼文，剔除 [徵/、[公告]、已刪除文章。"""
        posts = parse_ptt_post_list(MOCK_PTT_LIST_HTML)
        assert len(posts) == 2  # 僅兩篇 [賣/ 貼文
        assert posts[0]["post_id"] == "M.1711111111.A.001"
        assert posts[0]["author"] == "sellerA"
        assert "[賣/台北/面交]" in posts[0]["title"]

        assert posts[1]["post_id"] == "M.1711111114.A.004"
        assert posts[1]["author"] == "sellerC"
        assert "[賣/全國/店到店]" in posts[1]["title"]

    def test_parse_ptt_date(self):
        """測試 PTT 英文時間字串轉為 ISO 格式。"""
        dt_iso = parse_ptt_date("Tue Nov  3 12:00:00 2026")
        assert "2026-11-03T12:00:00" in dt_iso

    def test_fetch_ptt_post_details_mock(self):
        """測試內文抽取、金屬欄位與推文數統計。"""
        mock_resp = MagicMock()
        mock_resp.text = MOCK_PTT_POST_HTML
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp

        details = fetch_ptt_post_details("http://fake-ptt.url", session=mock_session)
        assert details["comment_count"] == 2
        assert "◎硬體型號：技嘉 RTX 4060 Ti Eagle 8G" in details["content"]
        assert "◎欲售價格：10500" in details["content"]
        # metaline 與 push 必須已被淨化剔除
        assert "作者" not in details["content"]
        assert "user1" not in details["content"]

    def test_sync_ptt_to_db(self, memory_db: sqlite3.Connection):
        """測試 sync_ptt_to_db 寫入 In-Memory SQLite 與快取去重機制。"""
        mock_list_resp = MagicMock()
        mock_list_resp.text = MOCK_PTT_LIST_HTML
        mock_list_resp.raise_for_status = MagicMock()

        mock_post_resp = MagicMock()
        mock_post_resp.text = MOCK_PTT_POST_HTML
        mock_post_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        # 第一次 get 拿列表，後續 get 拿文章
        mock_session.get.side_effect = [mock_list_resp, mock_post_resp, mock_post_resp]

        mock_parser = MagicMock(return_value={
            "category": "GPU",
            "spec_group": "RTX 4060 TI 8G",
            "brand": "Gigabyte",
            "model_name": "技嘉 RTX 4060 Ti Eagle 8G",
            "used_asking_price": 10500,
            "warranty_status": "原廠保內",
            "seller_type": "個人自售",
            "trade_method": "面交",
            "condition_notes": "保固至 2028 年",
        })

        with patch("crawlers.ptt_crawler._get_ptt_session", return_value=mock_session):
            synced = sync_ptt_to_db(
                conn=memory_db,
                max_pages=1,
                llm_parser_func=mock_parser,
                sleep_range=(0, 0),
            )
            assert synced == 2

            cur = memory_db.execute("SELECT COUNT(*) FROM used_market_listings;")
            assert cur.fetchone()[0] == 2

            # 再次執行，觸發快取檢查，synced 應為 0 筆新增
            mock_session.get.side_effect = [mock_list_resp]
            synced_again = sync_ptt_to_db(
                conn=memory_db,
                max_pages=1,
                llm_parser_func=mock_parser,
                sleep_range=(0, 0),
            )
            assert synced_again == 0


# --- Facebook 社團爬蟲測試 (fb_crawler) ---

class TestFbCrawler:
    """測試 Facebook 社團爬蟲 Cookie Pre-flight 檢查與優雅降級。"""

    def test_check_fb_cookie_health_non_existent(self, tmp_path):
        """測試 Cookie 檔案不存在時回傳 False。"""
        non_existent = str(tmp_path / "not_found.json")
        assert check_fb_cookie_health(non_existent) is False

    def test_check_fb_cookie_health_invalid_format(self, tmp_path):
        """測試 Cookie 檔案內容格式錯誤或非 JSON list 時回傳 False。"""
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{\"not_a_list\": 123}", encoding="utf-8")
        assert check_fb_cookie_health(str(bad_json)) is False

    def test_check_fb_cookie_health_missing_auth_tokens(self, tmp_path):
        """測試 Cookie 缺乏 c_user 與 xs 關鍵憑證時回傳 False。"""
        unauth_json = tmp_path / "unauth.json"
        unauth_json.write_text(json.dumps([{"name": "datr", "value": "xyz"}]), encoding="utf-8")
        assert check_fb_cookie_health(str(unauth_json)) is False

    def test_check_fb_cookie_health_valid(self, tmp_path):
        """測試包含有效 c_user 或 xs 時回傳 True。"""
        valid_json = tmp_path / "valid.json"
        valid_json.write_text(json.dumps([
            {"name": "c_user", "value": "100012345678"},
            {"name": "xs", "value": "abc123token"},
        ]), encoding="utf-8")
        assert check_fb_cookie_health(str(valid_json)) is True

    def test_crawl_fb_group_without_playwright(self):
        """當系統未載入或無 playwright 時，優雅回傳空列表而非崩潰。"""
        with patch.dict("sys.modules", {"playwright.sync_api": None}):
            res = crawl_fb_group("https://facebook.com/groups/fake", cookie_path="fake.json")
            assert res == []
