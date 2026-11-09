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

import datetime
import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from crawlers.coolpc_crawler import (
    _extract_prices_and_change,
    parse_coolpc_html,
    sync_coolpc_to_db,
)
from crawlers.fb_crawler import (
    _load_cookies_into_context,
    _parse_cookie_file,
    check_fb_cookie_health,
    crawl_fb_group,
)
from crawlers.ptt_crawler import (
    fetch_ptt_page,
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


# --- PTT 例外處理兩層規則回歸測試 ---

_PTT_LOGGER = "crawlers.ptt_crawler"

_MOCK_PARSED = {
    "category": "GPU",
    "spec_group": "RTX 4060 TI 8G",
    "brand": "Gigabyte",
    "model_name": "技嘉 RTX 4060 Ti Eagle 8G",
    "used_asking_price": 10500,
    "warranty_status": "原廠保內",
    "seller_type": "個人自售",
    "trade_method": "面交",
    "condition_notes": "",
}


def _html_resp(html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


def _ptt_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _PTT_LOGGER and r.levelno == logging.WARNING]


def _run_ptt_sync(conn: sqlite3.Connection, session: MagicMock, max_pages: int = 1, parser=None) -> int:
    with patch("crawlers.ptt_crawler._get_ptt_session", return_value=session):
        return sync_ptt_to_db(
            conn=conn,
            max_pages=max_pages,
            llm_parser_func=parser or MagicMock(return_value=dict(_MOCK_PARSED)),
            sleep_range=(0, 0),
        )


class TestPttExceptionHandling:
    """ptt_crawler 只捕捉 RequestException（warning 後 break／continue），其他例外要往外拋。"""

    def test_parse_ptt_date_invalid_format_warns_and_returns_iso(self, caplog: pytest.LogCaptureFixture):
        """日期格式錯誤 → warning（含原始值）並回傳可被 fromisoformat 解析的 ISO 字串。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        res = parse_ptt_date("2026/11/03 12:00")
        assert isinstance(datetime.datetime.fromisoformat(res), datetime.datetime)
        warnings = _ptt_warnings(caplog)
        assert len(warnings) == 1
        assert "2026/11/03 12:00" in warnings[0].getMessage()

    @pytest.mark.parametrize("bad", ["", "   ", "Xyz Nov  3 12:00:00 2026", "Tue Nov 31 12:00:00 2026"])
    def test_parse_ptt_date_edge_inputs_warn(self, bad: str, caplog: pytest.LogCaptureFixture):
        """空字串、只有空白、錯誤星期、不存在的日期 → 都是 ValueError，warning 後回傳 ISO 字串而不拋例外。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        res = parse_ptt_date(bad)
        datetime.datetime.fromisoformat(res)
        assert _ptt_warnings(caplog)

    def test_parse_ptt_date_valid_does_not_warn(self, caplog: pytest.LogCaptureFixture):
        """正常日期（含 PTT 的雙空格）不記 warning。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        assert parse_ptt_date("Tue Nov  3 12:00:00 2026") == "2026-11-03T12:00:00"
        assert _ptt_warnings(caplog) == []

    def test_fetch_details_bad_time_metaline_warns(self, caplog: pytest.LogCaptureFixture):
        """文章的時間欄位格式錯誤 → post_time 仍是 ISO 字串、有 warning，內文照常抽取。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        html = MOCK_PTT_POST_HTML.replace("Tue Nov  3 12:00:00 2026", "not a date")
        session = MagicMock()
        session.get.return_value = _html_resp(html)
        details = fetch_ptt_post_details("http://fake-ptt.url", session=session)
        datetime.datetime.fromisoformat(details["post_time"])
        assert "◎欲售價格：10500" in details["content"]
        assert _ptt_warnings(caplog)

    def test_fetch_functions_pass_timeout(self):
        """fetch_ptt_page／fetch_ptt_post_details 的網路請求一定帶 timeout。"""
        session = MagicMock()
        session.get.return_value = _html_resp(MOCK_PTT_LIST_HTML)
        fetch_ptt_page("http://fake-ptt.url/index.html", session=session)
        assert session.get.call_args.kwargs.get("timeout") == 10
        session.get.return_value = _html_resp(MOCK_PTT_POST_HTML)
        fetch_ptt_post_details("http://fake-ptt.url/post.html", session=session, timeout=3)
        assert session.get.call_args.kwargs.get("timeout") == 3

    @pytest.mark.parametrize(
        "exc",
        [
            requests.exceptions.ConnectionError("conn refused"),
            requests.exceptions.Timeout("read timeout"),
            requests.exceptions.HTTPError("503 Server Error"),
        ],
        ids=["ConnectionError", "Timeout", "HTTPError"],
    )
    def test_list_page_request_exception_breaks_with_warning(
        self, memory_db: sqlite3.Connection, exc: Exception, caplog: pytest.LogCaptureFixture
    ):
        """列表頁 RequestException → warning（含 URL 與錯誤）後 break，回傳 0，不寫任何資料。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        session = MagicMock()
        session.get.side_effect = exc
        parser = MagicMock(return_value=dict(_MOCK_PARSED))
        assert _run_ptt_sync(memory_db, session, max_pages=3, parser=parser) == 0
        assert session.get.call_count == 1  # break，不再嘗試後續頁
        parser.assert_not_called()
        warnings = _ptt_warnings(caplog)
        assert len(warnings) == 1
        msg = warnings[0].getMessage()
        assert "HardwareSale/index.html" in msg
        assert str(exc) in msg
        assert memory_db.execute("SELECT COUNT(*) FROM used_market_listings;").fetchone()[0] == 0

    def test_list_page_raise_for_status_http_error_breaks(
        self, memory_db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
    ):
        """resp.raise_for_status() 拋 HTTPError（RequestException 子類）→ 一樣 warning 後 break。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        resp = _html_resp(MOCK_PTT_LIST_HTML)
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("403 Forbidden")
        session = MagicMock()
        session.get.return_value = resp
        assert _run_ptt_sync(memory_db, session) == 0
        assert _ptt_warnings(caplog)

    def test_second_list_page_failure_keeps_first_page_results(
        self, memory_db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
    ):
        """第 1 頁成功、第 2 頁網路失敗 → 保留第 1 頁寫入的 2 筆並回傳 2，有 warning。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        session = MagicMock()
        session.get.side_effect = [
            _html_resp(MOCK_PTT_LIST_HTML),
            _html_resp(MOCK_PTT_POST_HTML),
            _html_resp(MOCK_PTT_POST_HTML),
            requests.exceptions.ConnectionError("page 2 down"),
        ]
        assert _run_ptt_sync(memory_db, session, max_pages=2) == 2
        assert memory_db.execute("SELECT COUNT(*) FROM used_market_listings;").fetchone()[0] == 2
        warnings = _ptt_warnings(caplog)
        assert len(warnings) == 1
        assert "index1234.html" in warnings[0].getMessage()

    def test_post_detail_request_exception_skips_post_with_warning(
        self, memory_db: sqlite3.Connection, caplog: pytest.LogCaptureFixture
    ):
        """第 1 篇內文 RequestException → warning（含文章 URL）後 continue，第 2 篇照常寫入。"""
        caplog.set_level(logging.WARNING, logger=_PTT_LOGGER)
        session = MagicMock()
        session.get.side_effect = [
            _html_resp(MOCK_PTT_LIST_HTML),
            requests.exceptions.Timeout("post timeout"),
            _html_resp(MOCK_PTT_POST_HTML),
        ]
        assert _run_ptt_sync(memory_db, session) == 1
        ids = [r[0] for r in memory_db.execute("SELECT post_id FROM used_market_listings;")]
        assert ids == ["M.1711111114.A.004"]
        warnings = _ptt_warnings(caplog)
        assert len(warnings) == 1
        assert "M.1711111111.A.001" in warnings[0].getMessage()
        assert "post timeout" in warnings[0].getMessage()

    def test_skipped_post_is_retried_next_sync(self, memory_db: sqlite3.Connection):
        """網路失敗而跳過的文章沒有入庫，下次同步會重新抓取（不被快取擋掉）。"""
        session = MagicMock()
        session.get.side_effect = [
            _html_resp(MOCK_PTT_LIST_HTML),
            requests.exceptions.ConnectionError("down"),
            _html_resp(MOCK_PTT_POST_HTML),
        ]
        assert _run_ptt_sync(memory_db, session) == 1
        session.get.side_effect = [_html_resp(MOCK_PTT_LIST_HTML), _html_resp(MOCK_PTT_POST_HTML)]
        assert _run_ptt_sync(memory_db, session) == 1
        assert memory_db.execute("SELECT COUNT(*) FROM used_market_listings;").fetchone()[0] == 2

    def test_list_page_non_request_exception_propagates(self, memory_db: sqlite3.Connection):
        """列表頁發生非網路錯誤（例如 RuntimeError）→ 不可被吞掉，要往外拋給 services 邊界。"""
        session = MagicMock()
        session.get.side_effect = RuntimeError("unexpected bug")
        with pytest.raises(RuntimeError, match="unexpected bug"):
            _run_ptt_sync(memory_db, session)

    def test_list_parse_bug_propagates(self, memory_db: sqlite3.Connection):
        """列表頁解析程式本身的 bug（parse_ptt_post_list 拋 TypeError）→ 往外拋。"""
        session = MagicMock()
        session.get.return_value = _html_resp(MOCK_PTT_LIST_HTML)
        with patch("crawlers.ptt_crawler.parse_ptt_post_list", side_effect=TypeError("parser bug")):
            with pytest.raises(TypeError, match="parser bug"):
                _run_ptt_sync(memory_db, session)

    @pytest.mark.parametrize("exc_type", [RuntimeError, AttributeError, KeyError, ValueError])
    def test_post_detail_non_request_exception_propagates(
        self, memory_db: sqlite3.Connection, exc_type: type[Exception]
    ):
        """內文抓取／解析的非網路錯誤（含 ValueError、KeyError 等）→ 不可 continue 吞掉，要往外拋。"""
        session = MagicMock()
        session.get.return_value = _html_resp(MOCK_PTT_LIST_HTML)
        with patch("crawlers.ptt_crawler.fetch_ptt_post_details", side_effect=exc_type("detail bug")):
            with pytest.raises(exc_type):
                _run_ptt_sync(memory_db, session)
        assert memory_db.execute("SELECT COUNT(*) FROM used_market_listings;").fetchone()[0] == 0

    def test_parser_exception_propagates(self, memory_db: sqlite3.Connection):
        """注入的 llm_parser_func 拋非預期例外 → 往外拋，不被當成網路錯誤跳過。"""
        session = MagicMock()
        session.get.side_effect = [_html_resp(MOCK_PTT_LIST_HTML), _html_resp(MOCK_PTT_POST_HTML)]
        with pytest.raises(RuntimeError, match="llm bug"):
            _run_ptt_sync(memory_db, session, parser=MagicMock(side_effect=RuntimeError("llm bug")))

    def test_missing_price_stored_as_zero(self, memory_db: sqlite3.Connection):
        """解析結果沒有價格 → 新抓到的刊登存 0（未開價標記，ERR-015），不是 None 也不跳過。"""
        parsed = dict(_MOCK_PARSED)
        parsed.pop("used_asking_price")
        session = MagicMock()
        session.get.side_effect = [
            _html_resp(MOCK_PTT_LIST_HTML),
            _html_resp(MOCK_PTT_POST_HTML),
            _html_resp(MOCK_PTT_POST_HTML),
        ]
        assert _run_ptt_sync(memory_db, session, parser=MagicMock(return_value=parsed)) == 2
        prices = [r[0] for r in memory_db.execute("SELECT used_asking_price FROM used_market_listings;")]
        assert prices == [0, 0]


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


# --- FB 例外處理兩層規則回歸測試（Playwright 以 mock 取代，不啟動瀏覽器） ---

_FB_LOGGER = "fb_crawler"
_GROUP_URL = "https://www.facebook.com/groups/fake-hw"
_NETSCAPE_HEADER = "# Netscape HTTP Cookie File\n"


def _fb_records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _FB_LOGGER and r.levelno == level]


def _mock_playwright() -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """回傳 (sync_playwright mock, browser, context, page)；page 預設沒有貼文、沒有彈窗。"""
    sp = MagicMock(name="sync_playwright")
    p = sp.return_value.__enter__.return_value
    browser = p.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.url = _GROUP_URL
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    return sp, browser, context, page


def _feed_item(text: str) -> MagicMock:
    item = MagicMock()
    item.inner_text.return_value = text
    item.query_selector.return_value = None
    return item


def _crawl(sp: MagicMock, tmp_path, **kwargs) -> list[dict]:
    cookie = str(tmp_path / "no_cookie.json")
    with patch("playwright.sync_api.sync_playwright", sp), patch("crawlers.fb_crawler.time.sleep"):
        return crawl_fb_group(_GROUP_URL, cookie_path=cookie, **kwargs)


class TestFbCookieFileExceptionHandling:
    """_parse_cookie_file：float(expires) 只捕捉 (TypeError, ValueError)；讀檔只捕捉 (OSError, ValueError) 並 warning。"""

    @pytest.mark.parametrize("bad_expires", ["notanumber", "", "12,34"])
    def test_netscape_bad_expires_keeps_cookie_without_expires(self, tmp_path, bad_expires: str):
        """expires 不是數字 → 該 cookie 仍保留（視為 session cookie），只是沒有 expires 欄位，其他欄位不受影響。"""
        f = tmp_path / "cookies.txt"
        f.write_text(
            _NETSCAPE_HEADER
            + f".facebook.com\tTRUE\t/\tTRUE\t{bad_expires}\tc_user\t100012345678\n"
            + ".facebook.com\tTRUE\t/\tTRUE\t1893456000\txs\tabc\n",
            encoding="utf-8",
        )
        cookies = _parse_cookie_file(str(f))
        assert len(cookies) == 2
        assert cookies[0]["name"] == "c_user" and cookies[0]["value"] == "100012345678"
        assert "expires" not in cookies[0]
        assert cookies[0]["secure"] is True
        assert cookies[1]["expires"] == 1893456000.0
        assert check_fb_cookie_health(str(f)) is True

    def test_netscape_bad_expires_is_logged(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """AGENTS.md：欄位解析失敗要用 logging 記下欄位與原始值；expires 解析失敗不應完全靜默。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        f = tmp_path / "cookies.txt"
        f.write_text(
            _NETSCAPE_HEADER + ".facebook.com\tTRUE\t/\tTRUE\tnotanumber\tc_user\t1\n",
            encoding="utf-8",
        )
        _parse_cookie_file(str(f))
        logged = [r for r in caplog.records if r.name == _FB_LOGGER and "notanumber" in r.getMessage()]
        assert logged, "expires='notanumber' 解析失敗時沒有任何 log（靜默略過）"

    def test_missing_cookie_file_returns_empty_without_raising(self, tmp_path):
        """Cookie 檔不存在 → 回傳 []、health 為 False（這是公開模式的正常情境，warning 由 crawl_fb_group 記）。"""
        missing = str(tmp_path / "nope.json")
        assert _parse_cookie_file(missing) == []
        assert check_fb_cookie_health(missing) is False

    def test_missing_cookie_file_warns_in_crawl(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """Cookie 檔不存在時 crawl_fb_group 要記 warning（含路徑），並以公開模式繼續。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        sp, _, context, _ = _mock_playwright()
        assert _crawl(sp, tmp_path, max_posts=1) == []
        msgs = [r.getMessage() for r in _fb_records(caplog, logging.WARNING)]
        assert any("no_cookie.json" in m for m in msgs)
        context.add_cookies.assert_not_called()

    @pytest.mark.parametrize(
        "content",
        ["[not json", '{"name": "c_user", ', '[{"name": "c_user", "value": "1"},]'],
        ids=["broken-array", "truncated-object", "trailing-comma"],
    )
    def test_invalid_json_cookie_file_warns_and_returns_empty(
        self, tmp_path, content: str, caplog: pytest.LogCaptureFixture
    ):
        """非 JSON（JSONDecodeError 是 ValueError）→ warning（含檔案路徑）並回傳 []。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        f = tmp_path / "bad.json"
        f.write_text(content, encoding="utf-8")
        assert _parse_cookie_file(str(f)) == []
        assert check_fb_cookie_health(str(f)) is False
        warnings = _fb_records(caplog, logging.WARNING)
        assert warnings
        assert "bad.json" in warnings[0].getMessage()

    @pytest.mark.parametrize("content", ["[1, 2]", '["c_user"]', '[{"name": "c_user", "value": "1"}, 3]'])
    def test_non_dict_json_items_are_skipped_with_warning(
        self, tmp_path, content: str, caplog: pytest.LogCaptureFixture
    ):
        """JSON 陣列裡不是物件的項目 → 略過並 warning，check_fb_cookie_health 不崩潰。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        f = tmp_path / "odd.json"
        f.write_text(content, encoding="utf-8")
        cookies = _parse_cookie_file(str(f))
        assert all(isinstance(c, dict) for c in cookies)
        assert check_fb_cookie_health(str(f)) is (len(cookies) == 1)
        assert any("odd.json" in r.getMessage() for r in _fb_records(caplog, logging.WARNING))

    def test_non_utf8_cookie_file_warns_and_returns_empty(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """非 UTF-8 位元組（UnicodeDecodeError 是 ValueError）→ warning 並回傳 []。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        f = tmp_path / "big5.txt"
        f.write_bytes("餅乾".encode("big5") + b"\xff\xfe")
        assert _parse_cookie_file(str(f)) == []
        assert _fb_records(caplog, logging.WARNING)

    def test_os_error_on_open_warns_and_returns_empty(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """開檔遇到 OSError（例如 PermissionError）→ warning 並回傳 []。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        f = tmp_path / "locked.json"
        f.write_text("[]", encoding="utf-8")
        with patch("crawlers.fb_crawler.open", side_effect=PermissionError("denied"), create=True):
            assert _parse_cookie_file(str(f)) == []
        warnings = _fb_records(caplog, logging.WARNING)
        assert warnings and "denied" in warnings[0].getMessage()

    def test_unexpected_exception_while_reading_propagates(self, tmp_path):
        """讀檔時的非預期例外（RuntimeError）不在捕捉清單內，要往外拋。"""
        f = tmp_path / "c.json"
        f.write_text("[]", encoding="utf-8")
        with patch("crawlers.fb_crawler.json.loads", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError, match="bug"):
                _parse_cookie_file(str(f))

    def test_load_cookies_into_context_playwright_error_warns(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """context.add_cookies 拋 Playwright Error → warning 並回傳 False。"""
        caplog.set_level(logging.WARNING, logger=_FB_LOGGER)
        f = tmp_path / "c.json"
        f.write_text(json.dumps([{"name": "c_user", "value": "1"}]), encoding="utf-8")
        context = MagicMock()
        context.add_cookies.side_effect = PlaywrightError("Cookie should have a url or a domain/path pair")
        assert _load_cookies_into_context(context, str(f)) is False
        warnings = _fb_records(caplog, logging.WARNING)
        assert warnings and "domain/path" in warnings[0].getMessage()

    def test_load_cookies_into_context_success(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(json.dumps([{"name": "c_user", "value": "1"}]), encoding="utf-8")
        context = MagicMock()
        assert _load_cookies_into_context(context, str(f)) is True
        context.add_cookies.assert_called_once()

    def test_load_cookies_into_context_unexpected_error_propagates(self, tmp_path):
        """add_cookies 拋非 Playwright 的例外 → 往外拋（由 crawl_fb_group 最外層邊界處理）。"""
        f = tmp_path / "c.json"
        f.write_text(json.dumps([{"name": "c_user", "value": "1"}]), encoding="utf-8")
        context = MagicMock()
        context.add_cookies.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError):
            _load_cookies_into_context(context, str(f))


class TestFbCrawlExceptionHandling:
    """crawl_fb_group：goto／關閉彈窗只捕捉 Playwright Error；最外層邊界用 logger.exception。"""

    @pytest.mark.parametrize(
        "exc",
        [PlaywrightTimeoutError("Timeout 30000ms exceeded"), PlaywrightError("net::ERR_NAME_NOT_RESOLVED")],
        ids=["TimeoutError", "Error"],
    )
    def test_goto_playwright_error_warns_and_returns_empty(
        self, tmp_path, exc: Exception, caplog: pytest.LogCaptureFixture
    ):
        """page.goto 拋 Playwright Error／TimeoutError → warning（含錯誤）、關閉瀏覽器、回傳 []，不走最外層 exception。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, browser, _, page = _mock_playwright()
        page.goto.side_effect = exc
        assert _crawl(sp, tmp_path) == []
        browser.close.assert_called_once()
        assert any(str(exc) in r.getMessage() for r in _fb_records(caplog, logging.WARNING))
        assert _fb_records(caplog, logging.ERROR) == []

    def test_goto_called_with_timeout(self, tmp_path):
        sp, _, _, page = _mock_playwright()
        _crawl(sp, tmp_path, max_posts=1, timeout=12345)
        assert page.goto.call_args.kwargs.get("timeout") == 12345

    def test_goto_unexpected_error_goes_to_outer_boundary(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """page.goto 拋非 Playwright 例外 → 不被 goto 的 except 吞掉，由最外層 logger.exception（ERROR＋traceback）處理並回傳 []。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, _, _, page = _mock_playwright()
        page.goto.side_effect = RuntimeError("unexpected")
        assert _crawl(sp, tmp_path) == []
        errors = _fb_records(caplog, logging.ERROR)
        assert len(errors) == 1
        assert errors[0].exc_info is not None
        assert isinstance(errors[0].exc_info[1], RuntimeError)

    def test_close_popup_playwright_error_is_skipped_and_crawl_continues(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ):
        """關閉彈窗 click 拋 Playwright Error → 記 debug 後略過，繼續收集貼文。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, _, _, page = _mock_playwright()
        close_btn = MagicMock()
        close_btn.click.side_effect = PlaywrightError("Element is not attached to the DOM")
        page.query_selector.return_value = close_btn
        page.query_selector_all.return_value = [_feed_item("[賣] 技嘉 RTX 4060 Ti 8G 售 9500 台北面交")]
        posts = _crawl(sp, tmp_path, max_posts=1)
        assert len(posts) == 1
        assert posts[0]["post_url"] == _GROUP_URL
        debugs = _fb_records(caplog, logging.DEBUG)
        assert any("not attached" in r.getMessage() for r in debugs)
        assert _fb_records(caplog, logging.ERROR) == []

    def test_close_popup_unexpected_error_goes_to_outer_boundary(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """關閉彈窗拋非 Playwright 例外 → 不被略過，由最外層 logger.exception 處理。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, _, _, page = _mock_playwright()
        page.query_selector.side_effect = RuntimeError("selector bug")
        assert _crawl(sp, tmp_path, max_posts=1) == []
        errors = _fb_records(caplog, logging.ERROR)
        assert len(errors) == 1 and errors[0].exc_info is not None

    def test_mid_crawl_error_returns_partial_posts_with_exception_log(
        self, tmp_path, caplog: pytest.LogCaptureFixture
    ):
        """捲動途中出錯 → 最外層邊界記 logger.exception，並回傳已收集到的貼文（回傳值語意不變）。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, _, _, page = _mock_playwright()
        page.query_selector_all.return_value = [_feed_item("[賣] 金士頓 DDR5-6000 32G 售 2800 店到店")]
        page.evaluate.side_effect = PlaywrightError("Target page, context or browser has been closed")
        posts = _crawl(sp, tmp_path, max_posts=5)
        assert len(posts) == 1
        assert "DDR5-6000" in posts[0]["content"]
        errors = _fb_records(caplog, logging.ERROR)
        assert len(errors) == 1 and errors[0].exc_info is not None

    def test_browser_launch_failure_logs_exception(self, tmp_path, caplog: pytest.LogCaptureFixture):
        """chromium.launch 失敗（例如沒裝瀏覽器）→ 最外層 logger.exception，回傳 []。"""
        caplog.set_level(logging.DEBUG, logger=_FB_LOGGER)
        sp, _, _, _ = _mock_playwright()
        sp.return_value.__enter__.return_value.chromium.launch.side_effect = PlaywrightError(
            "Executable doesn't exist"
        )
        assert _crawl(sp, tmp_path) == []
        assert len(_fb_records(caplog, logging.ERROR)) == 1
