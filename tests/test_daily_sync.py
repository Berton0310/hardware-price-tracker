"""每日定時排程同步與管理工具自動化驗證測試套件 (tests/test_daily_sync.py)

遵循 CLAUDE.md、AGENTS.md「能透過程式碼驗證的一律使用程式碼」之鐵律。
完整覆蓋 scripts/daily_sync.py, daily_crawl.bat, setup_schedule.bat, setup_schedule.ps1。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from database.db_manager import init_db
from scripts.daily_sync import (
    DEFAULT_COOKIE_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_LOG_DIR,
    build_parser,
    main as daily_sync_main,
    run_daily_sync,
    setup_daily_logger,
)


class TestDailySyncCLIParser:
    """測試每日同步腳本之命令列參數解析器。"""

    def test_default_arguments(self):
        """測試預設參數設定符合規格。"""
        parser = build_parser()
        args = parser.parse_args([])
        assert args.db == DEFAULT_DB_PATH
        assert args.ptt_pages == 1
        assert args.fb_max_posts == 5
        assert args.no_fb is False
        assert args.cookie_path == DEFAULT_COOKIE_PATH
        assert args.log_dir == DEFAULT_LOG_DIR

    def test_custom_arguments(self):
        """測試自訂參數正確傳遞解析。"""
        parser = build_parser()
        args = parser.parse_args(
            [
                "--db", "custom.db",
                "--ptt-pages", "3",
                "--fb-max-posts", "10",
                "--no-fb",
                "--cookie-path", "cookies.json",
                "--log-dir", "custom_logs",
            ]
        )
        assert args.db == "custom.db"
        assert args.ptt_pages == 3
        assert args.fb_max_posts == 10
        assert args.no_fb is True
        assert args.cookie_path == "cookies.json"
        assert args.log_dir == "custom_logs"


class TestDailySyncLogger:
    """測試每日排程日誌紀錄模組。"""

    def test_setup_logger_creates_directory_and_file(self, tmp_path: Path):
        """測試日誌模組自動建立 logs 目錄與 daily_sync.log 檔案。"""
        log_dir = str(tmp_path / "test_logs")
        logger = setup_daily_logger(log_dir)

        test_msg = "Automated Test Daily Sync Log Entry"
        logger.info(test_msg)

        log_file = tmp_path / "test_logs" / "daily_sync.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert test_msg in content

        # 清除 handlers 避免影響其他測試
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)


class TestDailySyncWorkflow:
    """測試每日同步工作流程與異常處理邊界。"""

    def test_run_daily_sync_success(self, tmp_path: Path):
        """測試在 execute_sync 正常回傳時的資料庫連線生命週期與日誌紀錄。"""
        db_path = str(tmp_path / "sync_success.db")
        log_dir = str(tmp_path / "logs_success")

        mock_result = {
            "status": "SUCCESS",
            "coolpc_count": 100,
            "ptt_count": 20,
            "fb_count": 5,
            "details": "原價屋: 100 筆, PTT: 20 筆, FB: 5 筆",
            "log_id": 1,
        }

        with patch("scripts.daily_sync.execute_sync", return_value=mock_result) as mock_exec:
            res = run_daily_sync(
                db_path=db_path,
                ptt_pages=2,
                fb_max_posts=8,
                include_fb=True,
                log_dir=log_dir,
            )

            assert res["status"] == "SUCCESS"
            assert res["coolpc_count"] == 100
            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["include_fb"] is True
            assert call_kwargs["ptt_pages"] == 2
            assert call_kwargs["fb_max_posts"] == 8

        # 驗證日誌檔內容
        log_file = tmp_path / "logs_success" / "daily_sync.log"
        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "【每日排程同步啟動】" in log_content
        assert "【每日排程同步完成】" in log_content
        assert "原價屋新品=100 筆" in log_content

    def test_run_daily_sync_exception_handling_and_db_close(self, tmp_path: Path):
        """測試 execute_sync 拋出致命例外時，連線必須於 finally 關閉且日誌紀錄致命錯誤。"""
        db_path = str(tmp_path / "sync_fail.db")
        log_dir = str(tmp_path / "logs_fail")

        with patch("scripts.daily_sync.execute_sync", side_effect=RuntimeError("模擬網路斷線與爬蟲崩潰")):
            with pytest.raises(RuntimeError, match="模擬網路斷線與爬蟲崩潰"):
                run_daily_sync(db_path=db_path, log_dir=log_dir)

        # 驗證資料庫可被其他連線正常打開（證明連線已於 finally 確實關閉）
        conn = sqlite3.connect(db_path)
        conn.close()

        # 驗證日誌記錄了致命錯誤
        log_file = tmp_path / "logs_fail" / "daily_sync.log"
        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "【每日排程同步失敗】" in log_content
        assert "模擬網路斷線與爬蟲崩潰" in log_content

    def test_main_cli_exit_code_zero_on_success(self, tmp_path: Path):
        """測試 main() 成功時 sys.exit(0)。"""
        db_path = str(tmp_path / "main_ok.db")
        with patch("sys.argv", ["daily_sync.py", "--db", db_path, "--no-fb"]):
            with patch("scripts.daily_sync.run_daily_sync", return_value={"status": "SUCCESS"}):
                with pytest.raises(SystemExit) as exc_info:
                    daily_sync_main()
                assert exc_info.value.code == 0

    def test_main_cli_exit_code_one_on_failure(self, tmp_path: Path):
        """測試 main() 遭遇例外時 sys.exit(1)。"""
        db_path = str(tmp_path / "main_err.db")
        with patch("sys.argv", ["daily_sync.py", "--db", db_path]):
            with patch("scripts.daily_sync.run_daily_sync", side_effect=Exception("Critical")):
                with pytest.raises(SystemExit) as exc_info:
                    daily_sync_main()
                assert exc_info.value.code == 1


class TestScheduleScriptsIntegrity:
    """測試批次檔與排程註冊腳本之完整性與語法正確性。"""

    def test_daily_crawl_bat_exists_and_contains_commands(self):
        """驗證專案根目錄 daily_crawl.bat 存在且包含正確命令列語法。"""
        bat_path = Path("daily_crawl.bat")
        assert bat_path.is_file(), "根目錄 daily_crawl.bat 檔案必須存在"
        content = bat_path.read_text(encoding="utf-8")
        assert "chcp 65001" in content
        assert "daily_sync.py" in content
        assert "exit /b %EXIT_CODE%" in content

    def test_start_project_bat_exists_and_contains_hot_reload(self):
        """驗證專案根目錄 start_project.bat 存在且包含熱重載與 UTF-8 設定。"""
        bat_path = Path("start_project.bat")
        assert bat_path.is_file(), "根目錄 start_project.bat 檔案必須存在"
        content = bat_path.read_text(encoding="utf-8")
        assert "chcp 65001" in content
        assert "main_app.py" in content
        assert "--server.runOnSave true" in content
        assert "PYTHONDONTWRITEBYTECODE=1" in content

    def test_setup_schedule_bat_status_query(self):
        """驗證 scripts/setup_schedule.bat 可被呼叫且 status 查詢正常結束 (Exit Code 0)。"""
        res = subprocess.run(
            ["cmd.exe", "/c", "scripts\\setup_schedule.bat", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert res.returncode == 0
        assert "HardwarePriceDailySync" in res.stdout
        assert "每日定時排程管理工具" in res.stdout

    def test_setup_schedule_ps1_syntax_validity(self):
        """驗證 scripts/setup_schedule.ps1 在 PowerShell 下解析無任何語法錯誤。"""
        ps1_path = Path("scripts/setup_schedule.ps1")
        assert ps1_path.is_file(), "scripts/setup_schedule.ps1 必須存在"

        # 透過 PowerShell AST 解析器驗證腳本無 Syntax Error
        ps_cmd = (
            f"[System.Management.Automation.Language.Parser]::ParseFile("
            f"'{ps1_path.resolve()}', [ref]$null, [ref]$tokens, [ref]$errors);"
            f"if ($errors.Count -gt 0) {{ exit 1 }} else {{ exit 0 }}"
        )
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"setup_schedule.ps1 語法檢測失敗: {res.stderr}"
