"""每日排程資料同步專用腳本 (scripts/daily_sync.py)

遵循 CLAUDE.md、AGENTS.md 與「刪到不能再刪」極簡標準。
整合原價屋新品、PTT 二手板與 Facebook 社團之一鍵排程全量同步。
支援終端機與 logs/daily_sync.log 雙向日誌輸出、Windows UTF-8 終端與明確退出碼。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

# 解決 Windows 終端印出特殊 Unicode 字元之編碼問題
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 確保專案根目錄納入模組搜尋路徑
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database.db_manager import init_db
from services.sync_service import execute_sync

DEFAULT_DB_PATH = "data/hardware.db"
DEFAULT_COOKIE_PATH = "data/fb_cookies.json"
DEFAULT_LOG_DIR = "logs"


def setup_daily_logger(log_dir: str = DEFAULT_LOG_DIR) -> logging.Logger:
    """設定每日同步專用 Logger，同時輸出至終端機與檔案 logs/daily_sync.log。"""
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "daily_sync.log")

    logger = logging.getLogger("daily_sync")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 終端輸出 Handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # 檔案輸出 Handler (UTF-8)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


def run_daily_sync(
    db_path: str = DEFAULT_DB_PATH,
    ptt_pages: int = 1,
    fb_max_posts: int = 5,
    include_fb: bool = True,
    cookie_path: str = DEFAULT_COOKIE_PATH,
    log_dir: str = DEFAULT_LOG_DIR,
) -> dict[str, Any]:
    """執行每日排程資料同步核心工作流程。

    1. 初始化日誌輸出與資料庫連線。
    2. 調用 execute_sync 進行 CoolPC、PTT、FB 同步。
    3. 記錄各平台抓取筆數、耗時與狀態至日誌。
    4. 確保連線正常關閉，遇致命例外則記錄後向外拋出。
    """
    logger = setup_daily_logger(log_dir)
    start_time = datetime.now()
    start_ts = time.time()

    logger.info("=" * 64)
    logger.info(f"【每日排程同步啟動】時間: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(
        f"參數設定: DB={db_path}, PTT頁數={ptt_pages}, "
        f"FB社團同步={'啟用' if include_fb else '停用'} (上限={fb_max_posts}篇)"
    )
    logger.info("=" * 64)

    conn = init_db(db_path)
    try:
        res = execute_sync(
            conn=conn,
            include_fb=include_fb,
            ptt_pages=ptt_pages,
            fb_max_posts=fb_max_posts,
            cookie_path=cookie_path,
        )
        elapsed = time.time() - start_ts
        end_time = datetime.now()

        logger.info("-" * 64)
        logger.info(f"【每日排程同步完成】結束時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"執行狀態: {res.get('status')} | 總耗時: {elapsed:.2f} 秒")
        logger.info(
            f"抓取結果: 原價屋新品={res.get('coolpc_count', 0)} 筆, "
            f"PTT二手={res.get('ptt_count', 0)} 筆, "
            f"FB社團={res.get('fb_count', 0)} 筆"
        )
        logger.info(f"詳細日誌: {res.get('details', '')}")
        logger.info("=" * 64)
        return res
    except Exception as e:
        elapsed = time.time() - start_ts
        end_time = datetime.now()
        logger.error("-" * 64)
        logger.error(
            f"【每日排程同步失敗】時間: {end_time.strftime('%Y-%m-%d %H:%M:%S')} (耗時: {elapsed:.2f} 秒)"
        )
        logger.error(f"致命錯誤原因: {e}", exc_info=True)
        logger.error("=" * 64)
        raise
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    """建構命令列參數解析器。"""
    parser = argparse.ArgumentParser(
        description="電腦硬體價格比價系統 - 每日排程自動同步腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=str,
        default=DEFAULT_DB_PATH,
        help=f"指定 SQLite 資料庫路徑 (預設: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--ptt-pages",
        type=int,
        default=1,
        help="指定 PTT HardwareSale 二手板爬取頁數 (預設: 1)",
    )
    parser.add_argument(
        "--fb-max-posts",
        type=int,
        default=5,
        help="指定 Facebook 社團爬取最大貼文數 (預設: 5)",
    )
    parser.add_argument(
        "--no-fb",
        action="store_true",
        help="停用 Facebook 社團爬取 (僅同步原價屋與 PTT)",
    )
    parser.add_argument(
        "--cookie-path",
        type=str,
        default=DEFAULT_COOKIE_PATH,
        help=f"指定 Facebook Cookie 檔案路徑 (預設: {DEFAULT_COOKIE_PATH})",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=DEFAULT_LOG_DIR,
        help=f"指定日誌目錄 (預設: {DEFAULT_LOG_DIR})",
    )
    return parser


def main() -> None:
    """CLI 主程式進入點，成功返回 0，異常返回 1。"""
    parser = build_parser()
    args = parser.parse_args()

    try:
        run_daily_sync(
            db_path=args.db,
            ptt_pages=args.ptt_pages,
            fb_max_posts=args.fb_max_posts,
            include_fb=not args.no_fb,
            cookie_path=args.cookie_path,
            log_dir=args.log_dir,
        )
        sys.exit(0)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
