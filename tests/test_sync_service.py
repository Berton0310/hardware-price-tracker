"""即時同步服務模組單元測試 (tests/test_sync_service.py)

遵循 CLAUDE.md、AGENTS.md 與「刪到不能再刪」極簡標準。
客觀驗證 services/sync_service.py 的全平台排程同步、Cookie 判定、例外處理與資料庫日誌持久化。
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch
import pytest

from database.db_manager import get_latest_sync_log, init_db
from services.sync_service import execute_sync


@pytest.fixture
def mem_db() -> sqlite3.Connection:
    """提供乾淨的 In-Memory SQLite 資料庫。"""
    conn = init_db(":memory:")
    yield conn
    conn.close()


class TestSyncServiceExecution:
    """驗證 execute_sync 核心邏輯與多平台協同。"""

    def test_execute_sync_success_without_fb(self, mem_db: sqlite3.Connection):
        """Mock 原價屋與 PTT 成功，無 FB Cookie 時優雅跳過 FB 並成功寫入日誌。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=40) as mock_coolpc, \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=15) as mock_ptt, \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=False):

            result = execute_sync(mem_db, include_fb=True)

            mock_coolpc.assert_called_once()
            mock_ptt.assert_called_once()
            assert result["status"] == "SUCCESS"
            assert result["coolpc_count"] == 40
            assert result["ptt_count"] == 15
            assert result["fb_count"] == 0
            assert "原價屋: 40 筆, PTT: 15 筆, FB: 0 筆" in result["details"]

            # 驗證資料庫 sync_logs 持久化
            latest_log = get_latest_sync_log(mem_db)
            assert latest_log is not None
            assert latest_log["status"] == "SUCCESS"
            assert latest_log["coolpc_count"] == 40
            assert latest_log["ptt_count"] == 15
            assert latest_log["fb_count"] == 0

    def test_execute_sync_with_healthy_fb_cookie(self, mem_db: sqlite3.Connection):
        """當 FB Cookie 有效時，同步納入 FB 社團貼文。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=25), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=8), \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=True), \
             patch("crawlers.fb_crawler.sync_fb_to_db", return_value=5) as mock_fb:

            # 透過環境變數允許測試呼叫 FB 邏輯分支
            with patch.dict("os.environ", {"ENABLE_FB_TEST_CRAWL": "1"}):
                result = execute_sync(mem_db, include_fb=True, fb_max_posts=5)

            assert mock_fb.call_count >= 1
            assert result["status"] == "SUCCESS"
            assert result["coolpc_count"] == 25
            assert result["ptt_count"] == 8
            assert result["fb_count"] == 5

            latest_log = get_latest_sync_log(mem_db)
            assert latest_log is not None
            assert latest_log["fb_count"] == 5

    def test_execute_sync_fb_error_gracefully_skipped(self, mem_db: sqlite3.Connection):
        """FB 社團同步若發生網路或解析例外，應優雅跳過而不導致整體同步失敗。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=30), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=10), \
             patch("crawlers.fb_crawler.check_fb_cookie_health", return_value=True), \
             patch("crawlers.fb_crawler.sync_fb_to_db", side_effect=RuntimeError("FB Session Expired")):

            with patch.dict("os.environ", {"ENABLE_FB_TEST_CRAWL": "1"}):
                result = execute_sync(mem_db, include_fb=True)

            assert result["status"] == "SUCCESS"
            assert result["coolpc_count"] == 30
            assert result["ptt_count"] == 10
            assert result["fb_count"] == 0

    def test_execute_sync_coolpc_failure_raises_runtime_error(self, mem_db: sqlite3.Connection):
        """原價屋核心爬蟲拋出例外時，sync_logs 應持久化 FAILED 狀態並拋出 RuntimeError。"""
        with patch(
            "crawlers.coolpc_crawler.sync_coolpc_to_db",
            side_effect=ConnectionError("連線原價屋超時"),
        ), patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=0):

            with pytest.raises(RuntimeError) as exc_info:
                execute_sync(mem_db)

            assert "同步失敗" in str(exc_info.value)

            latest_log = get_latest_sync_log(mem_db)
            assert latest_log is not None
            assert latest_log["status"] == "FAILED"
            assert "原價屋: 連線原價屋超時" in latest_log["details"]

    def test_execute_sync_include_fb_false_skips_cookie_check(self, mem_db: sqlite3.Connection):
        """當 include_fb=False 時，不應進行 Cookie 健康檢查亦不呼叫 FB 爬蟲。"""
        with patch("crawlers.coolpc_crawler.sync_coolpc_to_db", return_value=10), \
             patch("crawlers.ptt_crawler.sync_ptt_to_db", return_value=5), \
             patch("crawlers.fb_crawler.check_fb_cookie_health") as mock_check:

            result = execute_sync(mem_db, include_fb=False)

            mock_check.assert_not_called()
            assert result["status"] == "SUCCESS"
            assert result["fb_count"] == 0
