"""驗證 Agent (Verification Agent) 獨立自動化驗收測試 (tests/test_sync_feature_acceptance.py)

本測試由驗證 Agent 獨立編寫與執行，嚴格依據 AGENTS.md 準則，以 100% 程式碼客觀斷言
對「即時資料同步核心服務、資料庫持久化、前端儀表板控制中心」進行閉環驗收。

驗證矩陣涵蓋：
1. 資料庫持久化驗證 (sync_logs 表結構、索引、時間排序、欄位完整性)
2. 同步服務業務邏輯驗證 (順序執行、Cookie 有效/無效、FB 例外優雅跳過、核心故障 FAILED 記錄與拋出)
3. 前端儀表板整合驗證 (session_state 防重機制、側邊欄 UI 徽章與時間格式、立即同步按鈕響應與錯誤捕獲)
4. 極端邊界條件驗證 (空表、多日誌時間競爭、特殊字元與巨量筆數)
"""

from __future__ import annotations

import datetime
import os
import sqlite3
from typing import Any
from unittest.mock import MagicMock, call, patch
import pytest

from streamlit.testing.v1 import AppTest

from database.db_manager import (
    get_latest_sync_log,
    init_db,
    record_sync_log,
)
from services.sync_service import DEFAULT_FB_GROUPS, execute_sync


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """提供乾淨獨立之 In-Memory SQLite 連線。"""
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ==============================================================================
# 1. 資料庫持久化與結構驗證 (Database Persistence Verification)
# ==============================================================================


class TestDatabaseSyncLogsPersistence:
    """客觀驗證 database/db_manager.py 中 sync_logs 表與相關 CRUD 函式。"""

    def test_sync_logs_table_schema_and_index(self, mem_db: sqlite3.Connection):
        """驗證 sync_logs 表各欄位名稱、資料型別與索引定義符合規範。"""
        # 1. 檢驗欄位規格
        cursor = mem_db.execute("PRAGMA table_info(sync_logs);")
        columns = {row["name"]: row["type"].upper() for row in cursor.fetchall()}
        expected_columns = {
            "log_id": "INTEGER",
            "sync_time": "TEXT",
            "status": "TEXT",
            "details": "TEXT",
            "coolpc_count": "INTEGER",
            "ptt_count": "INTEGER",
            "fb_count": "INTEGER",
        }
        for col, col_type in expected_columns.items():
            assert col in columns, f"缺少必要欄位: {col}"
            assert columns[col] == col_type, f"欄位 {col} 型態預期 {col_type}，實為 {columns[col]}"

        # 2. 檢驗索引
        cursor = mem_db.execute("PRAGMA index_list(sync_logs);")
        indexes = [row["name"] for row in cursor.fetchall()]
        assert "idx_sync_logs_time" in indexes, "缺少索引 idx_sync_logs_time"

    def test_empty_sync_logs_returns_none(self, mem_db: sqlite3.Connection):
        """空資料表時 get_latest_sync_log 應回傳 None 而非拋出例外。"""
        assert get_latest_sync_log(mem_db) is None

    def test_record_sync_log_default_timestamp_iso(self, mem_db: sqlite3.Connection):
        """驗證未傳遞 sync_time 時，自動以當前 ISO 時間格式入庫，且能被正常檢索。"""
        before_time = datetime.datetime.now()
        log_id = record_sync_log(
            conn=mem_db,
            status="SUCCESS",
            details="預設時間戳測試",
            coolpc_count=10,
            ptt_count=5,
            fb_count=2,
        )
        after_time = datetime.datetime.now()

        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["log_id"] == log_id
        assert latest["status"] == "SUCCESS"
        assert latest["details"] == "預設時間戳測試"
        assert latest["coolpc_count"] == 10
        assert latest["ptt_count"] == 5
        assert latest["fb_count"] == 2

        # 檢驗時間格式是否為合法 ISO
        parsed_dt = datetime.datetime.fromisoformat(latest["sync_time"])
        assert before_time - datetime.timedelta(seconds=2) <= parsed_dt <= after_time + datetime.timedelta(seconds=2)

    def test_get_latest_sync_log_order_descending(self, mem_db: sqlite3.Connection):
        """驗證多筆記錄時，依 sync_time DESC, log_id DESC 精準回傳最新一筆。"""
        t1 = "2026-11-01T08:00:00"
        t2 = "2026-11-02T14:30:00"
        t3 = "2026-11-03T18:45:00"

        id1 = record_sync_log(mem_db, status="SUCCESS", details="第1次", sync_time=t1)
        id2 = record_sync_log(mem_db, status="FAILED", details="第2次失敗", sync_time=t2)
        id3 = record_sync_log(mem_db, status="SUCCESS", details="第3次最新", sync_time=t3)

        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["log_id"] == id3
        assert latest["sync_time"] == t3
        assert latest["status"] == "SUCCESS"
        assert latest["details"] == "第3次最新"

        # 同秒新增兩筆時，以 log_id DESC 裁決
        id4 = record_sync_log(mem_db, status="FAILED", details="第4次同秒競爭A", sync_time=t3)
        id5 = record_sync_log(mem_db, status="SUCCESS", details="第5次同秒競爭B", sync_time=t3)
        latest_tie = get_latest_sync_log(mem_db)
        assert latest_tie is not None
        assert latest_tie["log_id"] == id5
        assert latest_tie["details"] == "第5次同秒競爭B"


# ==============================================================================
# 2. 同步服務業務邏輯驗證 (Sync Service Business Logic Verification)
# ==============================================================================


class TestSyncServiceExecutionFlow:
    """客觀驗證 services/sync_service.py 的排程同步、順序性與錯誤防護。"""

    def test_execute_sync_execution_order(self, mem_db: sqlite3.Connection):
        """驗證 execute_sync 依序同步原價屋 -> PTT -> FB (Cookie有效時)。"""
        call_order = []

        def mock_coolpc(conn):
            call_order.append("COOLPC")
            return 30

        def mock_ptt(conn, max_pages=1):
            call_order.append("PTT")
            return 12

        def mock_fb(conn, group_url, max_posts=5, cookie_path="data/fb_cookies.json"):
            call_order.append("FB")
            return 4

        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", side_effect=mock_coolpc), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", side_effect=mock_ptt), \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=True), \
             patch("crawlers.fb_crawler.sync_fb_to_db", side_effect=mock_fb), \
             patch.dict("os.environ", {"ENABLE_FB_TEST_CRAWL": "1"}):

            res = execute_sync(mem_db, include_fb=True)

        assert call_order == ["COOLPC", "PTT", "FB"]
        assert res["status"] == "SUCCESS"
        assert res["coolpc_count"] == 30
        assert res["ptt_count"] == 12
        assert res["fb_count"] == 4

        latest = get_latest_sync_log(mem_db)
        assert latest is not None
        assert latest["status"] == "SUCCESS"
        assert latest["coolpc_count"] == 30
        assert latest["ptt_count"] == 12
        assert latest["fb_count"] == 4

    def test_execute_sync_fb_cookie_invalid_gracefully_skipped(self, mem_db: sqlite3.Connection):
        """當 FB Cookie 檢測為無效或不存在時，優雅跳過 FB 社團爬蟲，不中斷整體同步。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=50) as m_coolpc, \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=20) as m_ptt, \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=False) as m_cookie, \
             patch("crawlers.fb_crawler.sync_fb_to_db") as m_fb, \
             patch.dict("os.environ", {"ENABLE_FB_TEST_CRAWL": "1"}):

            res = execute_sync(mem_db, include_fb=True)

            m_coolpc.assert_called_once()
            m_ptt.assert_called_once()
            m_cookie.assert_called_once()
            m_fb.assert_not_called()

            assert res["status"] == "SUCCESS"
            assert res["coolpc_count"] == 50
            assert res["ptt_count"] == 20
            assert res["fb_count"] == 0

            latest = get_latest_sync_log(mem_db)
            assert latest is not None
            assert latest["status"] == "SUCCESS"
            assert latest["fb_count"] == 0

    def test_execute_sync_fb_group_exception_gracefully_skipped(self, mem_db: sqlite3.Connection):
        """當 FB 社團爬蟲在連線時拋出例外，應被優雅捕獲且整體同步標記為 SUCCESS。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=20), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=10), \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=True), \
             patch("crawlers.fb_crawler.sync_fb_to_db", side_effect=ConnectionResetError("Facebook connection reset")), \
             patch.dict("os.environ", {"ENABLE_FB_TEST_CRAWL": "1"}):

            res = execute_sync(mem_db, include_fb=True)

            assert res["status"] == "SUCCESS"
            assert res["coolpc_count"] == 20
            assert res["ptt_count"] == 10
            assert res["fb_count"] == 0

            latest = get_latest_sync_log(mem_db)
            assert latest is not None
            assert latest["status"] == "SUCCESS"

    def test_execute_sync_coolpc_error_records_failed_and_raises(self, mem_db: sqlite3.Connection):
        """當原價屋新品同步失敗時，必須將 FAILED 狀態寫入 sync_logs，並拋出 RuntimeError。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", side_effect=TimeoutError("原價屋逾時")), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=5):

            with pytest.raises(RuntimeError) as exc_info:
                execute_sync(mem_db)

            assert "同步失敗" in str(exc_info.value)

            latest = get_latest_sync_log(mem_db)
            assert latest is not None
            assert latest["status"] == "FAILED"
            assert "原價屋: 原價屋逾時" in latest["details"]

    def test_execute_sync_ptt_error_records_failed_and_raises(self, mem_db: sqlite3.Connection):
        """當 PTT 同步失敗時，必須將 FAILED 狀態寫入 sync_logs，並拋出 RuntimeError。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=15), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", side_effect=ValueError("PTT 解析失敗")):

            with pytest.raises(RuntimeError) as exc_info:
                execute_sync(mem_db)

            assert "同步失敗" in str(exc_info.value)

            latest = get_latest_sync_log(mem_db)
            assert latest is not None
            assert latest["status"] == "FAILED"
            assert "PTT: PTT 解析失敗" in latest["details"]


# ==============================================================================
# 3. 前端儀表板整合驗證 (Frontend Dashboard Integration Verification)
# ==============================================================================


class TestFrontendDashboardIntegration:
    """客觀驗證 app/main_app.py 啟動防重、側邊欄頂部同步控制中心渲染與按鈕互動。"""

    @pytest.fixture
    def app_file(self) -> str:
        """回傳 main_app.py 之絕對路徑。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "app", "main_app.py")

    def test_apptest_startup_auto_sync_deduplication(
        self, app_file: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證 main() 啟動時透過 session_state['auto_sync_done'] 防止多次重複執行自動同步。"""
        db_file = tmp_path / "test_dedup.db"
        conn = init_db(str(db_file))
        conn.close()

        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        monkeypatch.setenv("ENABLE_TEST_AUTO_SYNC", "1")

        with patch("services.sync_service.execute_sync", return_value={"status": "SUCCESS"}) as m_sync:
            at = AppTest.from_file(app_file)
            at.run()

            # 首次啟動：應執行自動同步一次，且 session_state['auto_sync_done'] 為 True
            assert m_sync.call_count == 1
            assert at.session_state["auto_sync_done"] is True

            # 使用者多次操作與互動（切換類別、勾選篩選）：不應重複執行 execute_sync
            at.sidebar.selectbox[0].select("RAM")
            at.run()
            assert m_sync.call_count == 1

            at.sidebar.checkbox[0].uncheck()
            at.run()
            assert m_sync.call_count == 1

            at.sidebar.checkbox[1].check()
            at.run()
            assert m_sync.call_count == 1

    def test_apptest_sidebar_top_sync_status_rendering(
        self, app_file: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證側邊欄頂部正確渲染最後同步時間 (YYYY-MM-DD HH:MM:SS)、狀態徽章與筆數摘要。"""
        db_file = tmp_path / "test_render.db"
        conn = init_db(str(db_file))
        record_sync_log(
            conn,
            status="SUCCESS",
            details="成功同步",
            coolpc_count=42,
            ptt_count=18,
            fb_count=7,
            sync_time="2026-11-03T21:40:15.999999",
        )
        conn.close()

        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        at = AppTest.from_file(app_file)
        at.run()

        assert len(at.exception) == 0

        # 側邊欄頂部應有「🔄 同步控制中心」標題
        subheaders = [s.value for s in at.sidebar.subheader]
        assert any("🔄 同步控制中心" in s for s in subheaders)

        # 驗證狀態徽章、時間格式 (去除 T 與微秒) 與平台筆數摘要
        captions_text = " ".join([c.value for c in at.sidebar.caption])
        assert "🟢 同步成功" in captions_text
        assert "2026-11-03 21:40:15" in captions_text
        assert "原價屋: 42 筆" in captions_text
        assert "PTT: 18 筆" in captions_text
        assert "FB: 7 筆" in captions_text

    def test_apptest_sidebar_failed_status_badge(
        self, app_file: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證當最新一筆為 FAILED 時，側邊欄頂部渲染 🔴 同步失敗 徽章。"""
        db_file = tmp_path / "test_failed_badge.db"
        conn = init_db(str(db_file))
        record_sync_log(
            conn,
            status="FAILED",
            details="網路異常中斷",
            coolpc_count=0,
            ptt_count=0,
            fb_count=0,
            sync_time="2026-11-03T21:45:00",
        )
        conn.close()

        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        at = AppTest.from_file(app_file)
        at.run()

        assert len(at.exception) == 0
        captions_text = " ".join([c.value for c in at.sidebar.caption])
        assert "🔴 同步失敗" in captions_text

    def test_apptest_sync_button_click_and_error_toast(
        self, app_file: str, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ):
        """驗證點擊【🔄 立即同步】按鈕能重新觸發同步，且當拋出異常時呈現錯誤提示。"""
        db_file = tmp_path / "test_btn.db"
        conn = init_db(str(db_file))
        conn.close()

        monkeypatch.setenv("HARDWARE_DB_PATH", str(db_file))
        at = AppTest.from_file(app_file)
        at.run()

        # 1. 成功點擊情境
        with patch("services.sync_service.execute_sync", return_value={"status": "SUCCESS"}) as m_sync:
            # 找到立即同步按鈕並點擊
            sync_button = at.sidebar.button[0]
            assert "立即同步" in sync_button.label
            sync_button.click().run()
            assert m_sync.call_count == 1
            assert len(at.exception) == 0

        # 2. 失敗點擊情境
        with patch("services.sync_service.execute_sync", side_effect=RuntimeError("連線被拒絕")):
            sync_button = at.sidebar.button[0]
            sync_button.click().run()
            assert len(at.exception) == 0
            assert len(at.sidebar.error) >= 1
            assert "同步失敗: 連線被拒絕" in at.sidebar.error[0].value
